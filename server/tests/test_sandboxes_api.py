"""Comprehensive tests for Inspect AI Sandboxes runner, API, security, and RBAC."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.sandboxes.runner import (
    DockerSandbox,
    LocalSandbox,
    SandboxPolicyError,
    build_docker_run_args,
    execute_step,
    validate_sandbox_path,
)
from matrix_server.storage.models import SandboxRun


@pytest.fixture
def env(tmp_path):
    # Users, memberships and sessions of the branch's RBAC (G0); `main`'s JWT/register flow is gone.
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/sandboxes_test.db",
        span_store="memory",
        artifact_backend="local",
        artifact_local_dir=str(tmp_path / "artifacts"),
    )
    app = create_app(settings)
    client = TestClient(app)
    factory = app.state.session_factory
    project_a, roles_a = project_with_roles(factory, "Project A")
    project_b, roles_b = project_with_roles(factory, "Project B")

    return {
        "app": app,
        "client": client,
        "project_a": project_a,
        "project_b": project_b,
        "admin_a": roles_a["admin"],
        "member_a": roles_a["member"],
        "viewer_a": roles_a["viewer"],
        "admin_b": roles_b["admin"],
        "viewer_token_a": roles_a["viewer"],
        "member_token_a": roles_a["member"],
        "admin_token_a": roles_a["admin"],
        "admin_token_b": roles_b["admin"],
        "key_a": api_key_headers(factory, project_a),
    }


def test_local_sandbox_step_execution_and_truncation(tmp_path):
    sandbox = LocalSandbox(workdir=tmp_path / "box", timeout_seconds=10.0)

    # 1. file_edit step
    res_edit = execute_step(
        sandbox=sandbox,
        step_idx=1,
        tool_name="file_edit",
        tool_args={"path": "src/code.py", "content": "print('hello from code.py')\n"},
        thought="Write initial source file",
    )
    assert res_edit["exit_code"] == 0
    assert "written successfully" in res_edit["stdout"]
    assert sandbox.file_exists("src/code.py")
    assert "hello from code.py" in sandbox.read_file("src/code.py")

    # 2. python step
    res_py = execute_step(
        sandbox=sandbox,
        step_idx=2,
        tool_name="python",
        tool_args={"code": "import sys; sys.stdout.write('py_output'); sys.stderr.write('py_err')"},
        thought="Run python snippet",
    )
    assert res_py["exit_code"] == 0
    assert res_py["stdout"] == "py_output"
    assert res_py["stderr"] == "py_err"
    assert res_py["duration_ms"] > 0
    assert res_py["input_tokens"] > 0

    # 3. bash step
    res_bash = execute_step(
        sandbox=sandbox,
        step_idx=3,
        tool_name="bash",
        tool_args={"cmd": "echo 'bash_ok'"},
        thought="Run bash command",
    )
    assert res_bash["exit_code"] == 0
    assert "bash_ok" in res_bash["stdout"]

    # 4. Truncation test: generate > 64KB output (e.g. 100KB)
    large_code = "print('A' * 80000)"
    res_large = execute_step(
        sandbox=sandbox,
        step_idx=4,
        tool_name="python",
        tool_args={"code": large_code},
        thought="Run large output",
    )
    assert res_large["exit_code"] == 0
    # Output must be truncated to exactly 65536 bytes
    assert len(res_large["stdout"].encode("utf-8")) == 65536


def test_path_traversal_validation():
    with pytest.raises(ValueError, match="path traversal not allowed"):
        validate_sandbox_path("../../etc/passwd")

    with pytest.raises(ValueError, match="path traversal not allowed"):
        validate_sandbox_path("foo/../bar")

    with pytest.raises(ValueError, match="path must be relative"):
        validate_sandbox_path("/absolute/path")


# ---------------------------------------------------------------------------
# Hardened Docker Specification Tests
# ---------------------------------------------------------------------------


def test_docker_hardened_flags_enforcement():
    args = build_docker_run_args(
        image="python:3.11-slim",
        workdir_host="/tmp/host_workspace",
        sandbox_policy={"network_mode": "none"},
        is_admin=False,
    )

    # Strictly check all required hardened flags
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--user=65534:65534" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges:true" in args
    assert "--pids-limit=128" in args
    assert "--tmpfs=/tmp:rw,noexec,nosuid,size=256m" in args


def test_docker_socket_mounting_forbidden():
    # Attempting to mount docker.sock in docker args must raise SandboxPolicyError
    with pytest.raises(SandboxPolicyError, match="strictly forbidden"):
        build_docker_run_args(
            image="python:3.11-slim",
            workdir_host="/tmp/host_workspace",
            sandbox_policy={"mounts": ["/var/run/docker.sock:/var/run/docker.sock"]},
            is_admin=True,
        )


def test_docker_sandbox_graceful_fallback(tmp_path):
    # DockerSandbox should gracefully fall back to local execution if docker unavailable
    ds = DockerSandbox(
        workdir=tmp_path / "fallback_box",
        sandbox_policy={"task_timeout_seconds": 15},
    )
    ds.write_file("test.txt", "fallback content")
    assert ds.file_exists("test.txt")
    code, stdout, _stderr = ds.execute_bash("cat test.txt")
    assert code == 0
    assert "fallback content" in stdout


# ---------------------------------------------------------------------------
# API End-to-End Pipeline & Scorer Tests
# ---------------------------------------------------------------------------


def test_sandbox_run_execution_local_pipeline_and_scorers(env):
    client: TestClient = env["client"]

    # 1. Successful run with unit_test scorer and verified artifacts
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "unit-test-evaluation-run",
            "runner_backend": "local_sandbox",
            "solver_config": {
                "name": "react",
                "steps": [
                    {
                        "tool_name": "file_edit",
                        "tool_args": {
                            "path": "calculator.py",
                            "content": "def add(a, b):\n    return a + b\n",
                        },
                        "thought": "Write calculator module",
                    },
                    {
                        "tool_name": "file_edit",
                        "tool_args": {
                            "path": "test_calculator.py",
                            "content": "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                        },
                        "thought": "Write unit tests",
                    },
                ],
            },
            "scorer_config": {
                "name": "unit_test",
                "command": "python -m pytest test_calculator.py",
                "artifacts": ["calculator.py"],
            },
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    run_data = res.json()
    run_id = run_data["id"]
    assert run_data["status"] == "completed"
    assert run_data["total_tasks"] == 1
    assert run_data["passed_tasks"] == 1
    assert run_data["pass_rate"] == 1.0
    assert run_data["total_tokens"] > 0
    assert run_data["total_cost_usd"] > 0

    # Check transcript details
    transcripts_res = client.get(
        f"/api/v1/sandboxes/runs/{run_id}/transcripts",
        headers=env["viewer_a"],
    )
    assert transcripts_res.status_code == 200
    t_list = transcripts_res.json()
    assert len(t_list) == 1
    assert t_list[0]["status"] == "passed"
    assert t_list[0]["score"] == 1.0

    # Check transcript single item details
    t_key = t_list[0]["task_key"]
    detail_res = client.get(
        f"/api/v1/sandboxes/runs/{run_id}/transcripts/{t_key}",
        headers=env["viewer_a"],
    )
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["task_key"] == t_key
    assert len(detail["steps"]) == 2
    assert detail["steps"][0]["tool_name"] == "file_edit"
    assert len(detail["verified_artifacts"]) >= 1
    art = detail["verified_artifacts"][0]
    assert art["path"] == "calculator.py"
    assert len(art["sha256"]) == 64
    assert art["size_bytes"] > 0


def test_scorer_file_exists_and_regex_and_pass_rate(env):
    client: TestClient = env["client"]

    # Multi-task run: Task 1 passes, Task 2 fails -> pass_rate = 0.5
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "multi-task-evaluation",
            "runner_backend": "local_sandbox",
            "solver_config": {
                "tasks": [
                    {
                        "task_key": "task_passing",
                        "prompt": "Create output.json",
                        "steps": [
                            {
                                "tool_name": "file_edit",
                                "tool_args": {"path": "output.json", "content": '{"status": "ok"}'},
                            }
                        ],
                    },
                    {
                        "task_key": "task_failing",
                        "prompt": "Do not create expected file",
                        "steps": [
                            {
                                "tool_name": "bash",
                                "tool_args": {"cmd": "echo 'doing nothing'"},
                            }
                        ],
                    },
                ]
            },
            "scorer_config": {
                "name": "file_exists",
                "files": ["output.json"],
            },
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["total_tasks"] == 2
    assert data["passed_tasks"] == 1
    assert data["pass_rate"] == 0.5


def test_scorer_artifact_hash(env):
    client: TestClient = env["client"]
    content = "deterministic content 12345"
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "artifact-hash-run",
            "runner_backend": "local_sandbox",
            "solver_config": {
                "steps": [
                    {
                        "tool_name": "file_edit",
                        "tool_args": {"path": "artifact.txt", "content": content},
                    }
                ]
            },
            "scorer_config": {
                "name": "artifact_hash",
                "hashes": {"artifact.txt": expected_sha},
            },
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    assert res.json()["pass_rate"] == 1.0


# ---------------------------------------------------------------------------
# Security & RBAC Tests
# ---------------------------------------------------------------------------


def test_security_non_admin_network_access_rejected(env):
    client: TestClient = env["client"]

    # Member attempting to enable bridge/host network mode -> 403 Forbidden
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "malicious-network-run",
            "sandbox_policy": {"network_mode": "bridge"},
        },
    )
    assert res.status_code == 403
    assert "requires role admin" in res.json()["detail"]

    # An API key is never ADMIN, even with every scope
    key_res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["key_a"],
        json={"name": "key-network-run", "sandbox_policy": {"network_mode": "bridge"}},
    )
    assert key_res.status_code == 403

    # The approval is server-owned: a client cannot claim it in the policy (T3)
    for forged in ({"is_admin": True}, {"network_approved_by": "someone"}):
        forged_res = client.post(
            "/api/v1/sandboxes/runs",
            headers=env["member_a"],
            json={"name": "forged", "sandbox_policy": {"network_mode": "bridge", **forged}},
        )
        assert forged_res.status_code == 422, forged_res.text
        assert "server-owned" in forged_res.text

    # Admin is allowed to request network_mode != 'none'
    admin_res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["admin_a"],
        json={
            "name": "admin-network-run",
            "sandbox_policy": {"network_mode": "bridge"},
        },
    )
    assert admin_res.status_code == 201
    with env["app"].state.session_factory() as db:
        stored = db.get(SandboxRun, admin_res.json()["id"]).sandbox_policy
    assert stored["network_mode"] == "bridge"
    assert stored["network_approved_by"]  # the approving admin's user id, set by the server


def test_runner_derives_network_approval_only_from_server_key():
    from matrix_server.sandboxes.runner import network_approved

    assert network_approved({"network_mode": "bridge", "network_approved_by": "user-1"}) is True
    assert network_approved({"network_mode": "bridge", "is_admin": True}) is False
    assert network_approved({}) is False


def test_security_docker_socket_mounting_forbidden(env):
    client: TestClient = env["client"]

    # Even admin cannot mount docker.sock
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["admin_a"],
        json={
            "name": "malicious-socket-mount",
            "sandbox_policy": {
                "mounts": ["/var/run/docker.sock:/var/run/docker.sock"],
            },
        },
    )
    assert res.status_code == 400
    assert "docker.sock is strictly forbidden" in res.json()["detail"]


def test_security_path_traversal_blocked(env):
    client: TestClient = env["client"]

    # Path traversal in task_key
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "path-traversal-task",
            "solver_config": {
                "tasks": [{"task_key": "../../escape_task"}]
            },
        },
    )
    assert res.status_code == 400
    assert "path traversal not allowed" in res.json()["detail"]

    # Path traversal in initial_files
    res_file = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "path-traversal-file",
            "solver_config": {
                "initial_files": {"../../etc/passwd": "root:x:0:0"}
            },
        },
    )
    assert res_file.status_code == 400


def test_security_anti_nan_rejection(env):
    client: TestClient = env["client"]

    # Using raw httpx content with NaN representation
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        content=b'{"name": "nan-run", "scorer_config": {"threshold": NaN}}',
    )
    assert res.status_code in (400, 422)


def test_rbac_and_cancellation(env):
    client: TestClient = env["client"]

    # Viewer cannot create a run (403)
    viewer_create = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["viewer_a"],
        json={"name": "viewer-forbidden-run"},
    )
    assert viewer_create.status_code == 403

    # Member can create run
    create_res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "cancellation-test-run"},
    )
    assert create_res.status_code == 201
    run_id = create_res.json()["id"]

    # Viewer cannot cancel run (403)
    viewer_cancel = client.post(
        f"/api/v1/sandboxes/runs/{run_id}/cancel",
        headers=env["viewer_a"],
    )
    assert viewer_cancel.status_code == 403

    # Member can cancel run (200)
    cancel_res = client.post(
        f"/api/v1/sandboxes/runs/{run_id}/cancel",
        headers=env["member_a"],
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"
    assert cancel_res.json()["finished_at"] is not None


def test_multi_tenant_isolation(env):
    client: TestClient = env["client"]

    # Member A creates a run in Project A
    res_a = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "project-a-private-run"},
    )
    assert res_a.status_code == 201
    run_a_id = res_a.json()["id"]

    # Admin B (Project B) tries to read run from Project A -> 404
    get_b = client.get(
        f"/api/v1/sandboxes/runs/{run_a_id}",
        headers=env["admin_b"],
    )
    assert get_b.status_code == 404

    # Admin B tries to cancel run from Project A -> 404
    cancel_b = client.post(
        f"/api/v1/sandboxes/runs/{run_a_id}/cancel",
        headers=env["admin_b"],
    )
    assert cancel_b.status_code == 404

    # Listing runs in Project B does not contain Project A's run
    list_b = client.get("/api/v1/sandboxes/runs", headers=env["admin_b"])
    assert list_b.status_code == 200
    b_ids = [r["id"] for r in list_b.json()["items"]]
    assert run_a_id not in b_ids
