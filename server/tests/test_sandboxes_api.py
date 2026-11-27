"""Comprehensive tests for Inspect AI Sandboxes runner, API, security, and RBAC (T7)."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles

import matrix_server.sandboxes as sandboxes_pkg
import matrix_server.sandboxes.runner as runner_mod
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.sandboxes.runner import (
    DockerSandbox,
    FakeSandbox,
    SandboxPolicyError,
    SandboxTask,
    SandboxUnavailableError,
    build_docker_run_args,
    evaluate_scorer,
    execute_step,
    is_docker_available,
    run_sandbox_job,
    validate_sandbox_path,
)
from matrix_server.storage.models import SandboxRun


def _require_docker() -> None:
    if not is_docker_available():
        pytest.skip("Docker daemon not available")


@pytest.fixture
def env(tmp_path):
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


# ---------------------------------------------------------------------------
# 1. Host Execution Removed & Fail-Closed Tests
# ---------------------------------------------------------------------------


def test_local_sandbox_is_completely_removed():
    assert not hasattr(sandboxes_pkg, "LocalSandbox")
    assert not hasattr(runner_mod, "LocalSandbox")


def test_fake_sandbox_requires_explicit_test_or_dev_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MATRIX_ENV", raising=False)
    with pytest.raises(SandboxPolicyError, match="MATRIX_ENV"):
        FakeSandbox(workdir=tmp_path / "box")

    monkeypatch.setenv("MATRIX_ENV", "production")
    with pytest.raises(SandboxPolicyError, match="MATRIX_ENV"):
        FakeSandbox(workdir=tmp_path / "box")

    monkeypatch.setenv("MATRIX_ENV", "test")
    sb = FakeSandbox(workdir=tmp_path / "box")
    assert sb.simulated is True
    assert sb.backend == "fake"


def test_fake_sandbox_never_executes_on_host(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "test")

    def _forbid(*args, **kwargs):
        raise AssertionError("Host execution must NEVER be invoked by FakeSandbox")

    monkeypatch.setattr(subprocess, "run", _forbid)
    monkeypatch.setattr(os, "system", _forbid)

    sandbox = FakeSandbox(workdir=tmp_path / "fake_box")
    res_bash = execute_step(sandbox, 1, "bash", {"cmd": "rm -rf /"})
    assert res_bash["exit_code"] == 0
    assert "[simulated:bash]" in res_bash["stdout"]

    res_py = execute_step(sandbox, 2, "python", {"code": "import os; os.system('id')"})
    assert res_py["exit_code"] == 0
    assert "[simulated:python]" in res_py["stdout"]

    # Deterministic output for same command
    res_bash2 = execute_step(sandbox, 3, "bash", {"cmd": "rm -rf /"})
    assert res_bash2["stdout"] == res_bash["stdout"]


def test_docker_dead_socket_fails_closed_without_host_execution(env, tmp_path, monkeypatch):
    """Point DOCKER_HOST at a dead socket and prove nothing ever executes on the host."""
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path / 'dead-docker.sock'}")

    def _forbid_os_system(*args, **kwargs):
        raise AssertionError("os.system must NEVER be called")

    monkeypatch.setattr(os, "system", _forbid_os_system)

    real_run = subprocess.run

    def _checked_run(cmd, *args, **kwargs):
        assert isinstance(cmd, list) and cmd[0] == "docker", f"Non-docker command attempted on host: {cmd}"
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _checked_run)

    with pytest.raises(SandboxUnavailableError):
        DockerSandbox(workdir=tmp_path / "box")

    # End-to-end via API (sync_exec=True in test env) -> status="sandbox_unavailable"
    client: TestClient = env["client"]
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "dead-docker-run",
            "runner_backend": "docker",
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "sandbox_unavailable"
    assert data["simulated"] is False
    assert "Docker daemon" in (data["error"] or "")


def test_path_traversal_validation():
    with pytest.raises(ValueError, match="path traversal not allowed"):
        validate_sandbox_path("../../etc/passwd")

    with pytest.raises(ValueError, match="path traversal not allowed"):
        validate_sandbox_path("foo/../bar")

    with pytest.raises(ValueError, match="path must be relative"):
        validate_sandbox_path("/absolute/path")


# ---------------------------------------------------------------------------
# 2. Hardened Docker Args, Allowlist, Minimal Env, Timeout, OOM, Truncation
# ---------------------------------------------------------------------------


def test_docker_hardened_flags_enforcement():
    args = build_docker_run_args(
        image="python:3.12-slim",
        workdir_host="/tmp/host_workspace",
        sandbox_policy={"network_mode": "none"},
        run_id="run123",
        sample_id="task1",
    )

    assert args[:3] == ["docker", "run", "--rm"]
    name_idx = args.index("--name")
    assert args[name_idx + 1].startswith("matrix-sbx-run123-task1-")
    assert "--init" in args
    assert args[args.index("--network") + 1] == "none"
    assert "--read-only" in args
    assert args[args.index("--user") + 1] == "65534:65534"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges:true"
    assert args[args.index("--pids-limit") + 1] == "128"
    assert args[args.index("--memory") + 1] == "512m"
    assert args[args.index("--memory-swap") + 1] == "512m"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=256m"
    assert args[args.index("--ipc") + 1] == "none"
    assert "--ulimit" in args
    assert args.count("-v") == 1
    assert args[args.index("-v") + 1] == "/tmp/host_workspace:/workspace:rw"
    assert args[-1] == "python:3.12-slim"


def test_image_allowlist_and_policy_rejection(monkeypatch):
    monkeypatch.setenv("MATRIX_SANDBOX_IMAGES", "python:3.12-slim,alpine:3.20")

    args = build_docker_run_args("alpine:3.20", "/tmp/ws")
    assert args[-1] == "alpine:3.20"

    with pytest.raises(SandboxPolicyError, match="allowlist"):
        build_docker_run_args("ubuntu:latest", "/tmp/ws")

    with pytest.raises(SandboxPolicyError, match="mounts/volumes"):
        build_docker_run_args("python:3.12-slim", "/tmp/ws", {"mounts": ["/var/run/docker.sock:/var/run/docker.sock"]})

    with pytest.raises(SandboxPolicyError, match="mounts/volumes"):
        build_docker_run_args("python:3.12-slim", "/tmp/ws", {"volumes": ["/etc:/host_etc"]})

    with pytest.raises(SandboxPolicyError, match="egress proxy"):
        build_docker_run_args("python:3.12-slim", "/tmp/ws", {"network_mode": "bridge"})

    with pytest.raises(SandboxPolicyError, match="cpus"):
        build_docker_run_args("python:3.12-slim", "/tmp/ws", {"cpus": 8})


def test_docker_cli_minimal_env_no_server_secret_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_SECRET_PROBE", "super-secret-token-xyz")
    monkeypatch.setenv("MATRIX_ADMIN_TOKEN", "admin-secret-abc")
    captured_envs: list[dict[str, str]] = []

    monkeypatch.setattr(runner_mod, "is_docker_available", lambda: True)

    def _fake_run(cmd, **kwargs):
        captured_envs.append(dict(kwargs.get("env") or {}))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    ds = DockerSandbox(workdir=tmp_path / "ws", run_id="r1", sample_id="s1")
    code, out, err = ds.execute_bash("env")
    assert code == 0
    assert out == "ok\n"
    assert err == ""
    assert len(captured_envs) == 1
    cli_env = captured_envs[0]
    assert "MATRIX_SECRET_PROBE" not in cli_env
    assert "MATRIX_ADMIN_TOKEN" not in cli_env
    assert cli_env.get("HOME") == ""
    assert "PATH" in cli_env


def test_docker_timeout_kills_and_removes_container(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "is_docker_available", lambda: True)
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0, output=b"partial")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    ds = DockerSandbox(workdir=tmp_path / "ws", run_id="r1", sample_id="s1")
    code, out, err = ds.execute_bash("sleep 60", timeout=1.0)
    assert code == -1
    assert out == "partial"
    assert "timed out" in err

    run_cmd = calls[0]
    cname = run_cmd[run_cmd.index("--name") + 1]
    assert ["docker", "kill", cname] in calls
    assert ["docker", "rm", "-f", cname] in calls


def test_docker_output_truncation_and_oom_137(env, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "is_docker_available", lambda: True)

    # 1. Truncation at 64 KiB
    def _large_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=b"A" * 100_000, stderr=b"B" * 100_000)

    monkeypatch.setattr(subprocess, "run", _large_run)
    ds = DockerSandbox(workdir=tmp_path / "ws1", run_id="r1", sample_id="s1")
    step = execute_step(ds, 1, "python", {"code": "print('A' * 100000)"})
    assert step["exit_code"] == 0
    assert len(step["stdout"].encode("utf-8")) == 65536
    assert len(step["stderr"].encode("utf-8")) == 65536

    # 2. OOM exit 137 -> transcript status == "oom"
    def _oom_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 137, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _oom_run)
    client: TestClient = env["client"]
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "oom-run",
            "runner_backend": "docker",
            "solver_config": {
                "steps": [{"tool_name": "python", "tool_args": {"code": "x = bytearray(10**9)"}}]
            },
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    run_id = res.json()["id"]
    assert res.json()["simulated"] is False

    t_list = client.get(f"/api/v1/sandboxes/runs/{run_id}/transcripts", headers=env["viewer_a"]).json()
    assert len(t_list) == 1
    assert t_list[0]["status"] == "oom"
    assert t_list[0]["simulated"] is False


# ---------------------------------------------------------------------------
# 3. API Pipeline & Scorers (using explicit `fake` backend in MATRIX_ENV=test)
# ---------------------------------------------------------------------------


def test_sandbox_run_fake_pipeline_scorers_and_artifacts(env, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "test")
    client: TestClient = env["client"]

    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "fake-pipeline-run",
            "runner_backend": "fake",
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
                ],
            },
            "scorer_config": {
                "name": "file_exists",
                "files": ["calculator.py"],
                "artifacts": ["calculator.py"],
            },
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    run_data = res.json()
    run_id = run_data["id"]
    assert run_data["status"] == "completed"
    assert run_data["simulated"] is True
    assert run_data["total_tasks"] == 1
    assert run_data["passed_tasks"] == 1
    assert run_data["pass_rate"] == 1.0

    transcripts_res = client.get(
        f"/api/v1/sandboxes/runs/{run_id}/transcripts",
        headers=env["viewer_a"],
    )
    assert transcripts_res.status_code == 200
    t_list = transcripts_res.json()
    assert len(t_list) == 1
    assert t_list[0]["status"] == "passed"
    assert t_list[0]["simulated"] is True

    t_key = t_list[0]["task_key"]
    detail = client.get(
        f"/api/v1/sandboxes/runs/{run_id}/transcripts/{t_key}",
        headers=env["viewer_a"],
    ).json()
    assert detail["simulated"] is True
    assert len(detail["verified_artifacts"]) == 1
    art = detail["verified_artifacts"][0]
    assert art["path"] == "calculator.py"
    assert len(art["sha256"]) == 64
    assert (art["storage_uri"] or "").startswith("artifact:")


def test_scorer_file_exists_and_artifact_hash(env, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "test")
    client: TestClient = env["client"]

    # Multi-task pass_rate = 0.5
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "multi-task-evaluation",
            "runner_backend": "fake",
            "solver_config": {
                "tasks": [
                    {
                        "task_key": "task_passing",
                        "steps": [
                            {"tool_name": "file_edit", "tool_args": {"path": "output.json", "content": '{"ok": true}'}}
                        ],
                    },
                    {
                        "task_key": "task_failing",
                        "steps": [{"tool_name": "bash", "tool_args": {"cmd": "echo nope"}}],
                    },
                ]
            },
            "scorer_config": {"name": "file_exists", "files": ["output.json"]},
            "sync_exec": True,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["total_tasks"] == 2
    assert data["passed_tasks"] == 1
    assert data["pass_rate"] == 0.5
    assert data["simulated"] is True

    # Artifact hash scorer
    content = "deterministic content 12345"
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    res_hash = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "artifact-hash-run",
            "runner_backend": "fake",
            "solver_config": {
                "steps": [{"tool_name": "file_edit", "tool_args": {"path": "artifact.txt", "content": content}}]
            },
            "scorer_config": {"name": "artifact_hash", "hashes": {"artifact.txt": expected_sha}},
            "sync_exec": True,
        },
    )
    assert res_hash.status_code == 201
    assert res_hash.json()["pass_rate"] == 1.0


# ---------------------------------------------------------------------------
# 4. Security, Policy Rejection (422), sync_exec guard, RBAC & Isolation
# ---------------------------------------------------------------------------


def test_security_policy_rejections_422(env, monkeypatch):
    client: TestClient = env["client"]

    # 1. local_sandbox backend is gone -> 422
    r_local = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "local-removed", "runner_backend": "local_sandbox"},
    )
    assert r_local.status_code == 422

    # 2. fake backend without MATRIX_ENV=test/dev -> 422
    monkeypatch.delenv("MATRIX_ENV", raising=False)
    r_fake = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "fake-blocked", "runner_backend": "fake"},
    )
    assert r_fake.status_code == 422

    # 3. network_mode != 'none' rejected even for ADMIN -> 422 with exact message
    for hdrs in (env["member_a"], env["admin_a"], env["key_a"]):
        r_net = client.post(
            "/api/v1/sandboxes/runs",
            headers=hdrs,
            json={"name": "net-run", "sandbox_policy": {"network_mode": "bridge"}},
        )
        assert r_net.status_code == 422
        assert "network access for sandboxes is not supported until an egress proxy exists" in r_net.json()["detail"]

    # 4. Server-owned keys rejected -> 422
    for forged in ({"is_admin": True}, {"network_approved_by": "someone"}):
        r_forged = client.post(
            "/api/v1/sandboxes/runs",
            headers=env["admin_a"],
            json={"name": "forged", "sandbox_policy": forged},
        )
        assert r_forged.status_code == 422
        assert "server-owned" in r_forged.text

    # 5. User mounts/volumes rejected even for ADMIN -> 422
    for bad_policy in (
        {"mounts": ["/var/run/docker.sock:/var/run/docker.sock"]},
        {"mounts": ["/tmp:/mnt"]},
        {"volumes": ["/etc:/etc"]},
    ):
        r_mnt = client.post(
            "/api/v1/sandboxes/runs",
            headers=env["admin_a"],
            json={"name": "mount-run", "sandbox_policy": bad_policy},
        )
        assert r_mnt.status_code == 422

    # 6. Image outside operator allowlist -> 422
    r_img = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["admin_a"],
        json={"name": "bad-img", "sandbox_policy": {"image": "evil/image:latest"}},
    )
    assert r_img.status_code == 422
    assert "allowlist" in r_img.json()["detail"]


def test_sync_exec_ignored_outside_test_env(env, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "production")
    client: TestClient = env["client"]

    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "prod-queued", "runner_backend": "docker", "sync_exec": True},
    )
    assert res.status_code == 201
    # Must remain queued for worker rather than executing synchronously
    assert res.json()["status"] == "queued"


def test_security_path_traversal_blocked(env):
    client: TestClient = env["client"]

    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "path-traversal-task", "solver_config": {"tasks": [{"task_key": "../../escape_task"}]}},
    )
    assert res.status_code == 400
    assert "path traversal not allowed" in res.json()["detail"]

    res_file = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "path-traversal-file", "solver_config": {"initial_files": {"../../etc/passwd": "root:x:0:0"}}},
    )
    assert res_file.status_code == 400


def test_security_anti_nan_rejection(env):
    client: TestClient = env["client"]
    res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        content=b'{"name": "nan-run", "scorer_config": {"threshold": NaN}}',
    )
    assert res.status_code in (400, 422)


def test_rbac_and_cancellation(env):
    client: TestClient = env["client"]

    viewer_create = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["viewer_a"],
        json={"name": "viewer-forbidden-run"},
    )
    assert viewer_create.status_code == 403

    create_res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "cancellation-test-run"},
    )
    assert create_res.status_code == 201
    run_id = create_res.json()["id"]
    assert create_res.json()["runner_backend"] == "docker"

    viewer_cancel = client.post(f"/api/v1/sandboxes/runs/{run_id}/cancel", headers=env["viewer_a"])
    assert viewer_cancel.status_code == 403

    cancel_res = client.post(f"/api/v1/sandboxes/runs/{run_id}/cancel", headers=env["member_a"])
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"
    assert cancel_res.json()["finished_at"] is not None


def test_multi_tenant_isolation(env):
    client: TestClient = env["client"]

    res_a = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={"name": "project-a-private-run"},
    )
    assert res_a.status_code == 201
    run_a_id = res_a.json()["id"]

    assert client.get(f"/api/v1/sandboxes/runs/{run_a_id}", headers=env["admin_b"]).status_code == 404
    assert client.post(f"/api/v1/sandboxes/runs/{run_a_id}/cancel", headers=env["admin_b"]).status_code == 404

    list_b = client.get("/api/v1/sandboxes/runs", headers=env["admin_b"])
    assert list_b.status_code == 200
    assert run_a_id not in [r["id"] for r in list_b.json()["items"]]


def test_dataset_commit_ownership_is_enforced(env):
    from matrix_server.storage.models import Dataset, DatasetCommit

    client: TestClient = env["client"]
    factory = env["app"].state.session_factory
    with factory() as s:
        ds_a = Dataset(project_id=env["project_a"], name="da")
        ds_b = Dataset(project_id=env["project_b"], name="db")
        s.add_all([ds_a, ds_b])
        s.flush()
        ca = DatasetCommit(project_id=env["project_a"], dataset_id=ds_a.id, tree_hash="h", message="m")
        cb = DatasetCommit(project_id=env["project_b"], dataset_id=ds_b.id, tree_hash="h", message="m")
        s.add_all([ca, cb])
        s.commit()
        ca_id, cb_id = ca.id, cb.id
    post = lambda body: client.post("/api/v1/sandboxes/runs", headers=env["member_a"], json={"name": "r", **body})  # noqa: E731
    foreign = post({"dataset_commit_id": cb_id})
    assert foreign.status_code == 404 and "commit" in foreign.json()["detail"]
    assert post({"dataset_commit_id": "0" * 32}).status_code == 404
    assert post({"dataset_commit_id": ca_id}).status_code == 201


# ---------------------------------------------------------------------------
# 5. Live Docker Containment Tests (skip with exact reason if no daemon)
# ---------------------------------------------------------------------------


@pytest.mark.docker
def test_live_docker_containment_uid_ro_no_net_no_env_leak(tmp_path, monkeypatch):
    _require_docker()
    monkeypatch.setenv("MATRIX_SECRET_PROBE", "should-never-enter-container")
    ds = DockerSandbox(workdir=tmp_path / "live_ws", run_id="live", sample_id="c1")

    # uid 65534
    code, out, _ = ds.execute_bash("id -u")
    assert code == 0 and out.strip() == "65534"

    # /etc read-only
    code, _, _ = ds.execute_bash("touch /etc/matrix_test_write")
    assert code != 0

    # no network interfaces besides lo
    # Some kernels (LinuxKit/OrbStack) list inactive tunnel devices even with --network none: only
    # an UP interface other than loopback would be a real network path.
    code, out, _ = ds.execute_python(
        "import os; d='/sys/class/net'; "
        "print(','.join(sorted(n for n in os.listdir(d) if n != 'lo' "
        "and os.path.isdir(os.path.join(d, n)) and open(os.path.join(d, n, 'operstate')).read().strip() not in ('down',))))"
    )
    assert code == 0 and out.strip() == ""

    # server env does not leak
    code, out, _ = ds.execute_bash("printenv MATRIX_SECRET_PROBE || true")
    assert code == 0 and "should-never-enter-container" not in out


@pytest.mark.docker
def test_live_docker_timeout_and_oom_leave_no_orphan_containers(tmp_path):
    _require_docker()
    ds = DockerSandbox(
        workdir=tmp_path / "live_oom",
        sandbox_policy={"memory": "64m", "pids_limit": 32},
        run_id="liveoom",
        sample_id="s1",
    )

    # Timeout kills and removes container
    code, _, err = ds.execute_bash("sleep 30", timeout=1.0)
    assert code == -1 and "timed out" in err

    # OOM returns 137
    code, _, _ = ds.execute_python("x = bytearray(256 * 1024 * 1024)", timeout=15.0)
    assert code == 137

    # No orphan containers
    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=matrix-sbx-liveoom", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ps.stdout.strip() == ""


# ---------------------------------------------------------------------------
# 6. T12 Scorer Fail-Closed & ArtifactStore Verification Tests
# ---------------------------------------------------------------------------


def test_unknown_scorer_rejected_at_creation_and_fails_closed(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "test")
    client: TestClient = env["client"]

    # 1. API rejects unknown scorer name with 422
    resp = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "unknown-scorer-run",
            "runner_backend": "fake",
            "scorer_config": {"name": "unit_test_typo"},
        },
    )
    assert resp.status_code == 422
    assert "Unsupported scorer" in resp.text

    # 2. Direct evaluate_scorer fails closed (status="failed", score=0.0)
    sb = FakeSandbox(workdir=tmp_path / "sb_unknown")
    status, score, details, _arts, err = evaluate_scorer(
        sandbox=sb,
        task=SandboxTask(task_key="t1"),
        scorer_config={"name": "nonexistent_scorer"},
        steps=[],
    )
    assert status == "failed"
    assert score == 0.0
    assert details["unknown_scorer"] == "nonexistent_scorer"
    assert "Unsupported scorer" in (err or "")


def test_llm_judge_requires_expected_output_and_fails_closed(env, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "test")
    client: TestClient = env["client"]

    # Without expected_output (even with spoofed score=1.0), llm_judge must fail closed
    resp_missing = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "llm-judge-no-expected",
            "runner_backend": "fake",
            "solver_config": {"steps": [{"tool_name": "bash", "tool_args": {"cmd": "echo 42"}}]},
            "scorer_config": {"name": "llm_judge", "rubric": "be accurate", "score": 1.0},
            "sync_exec": True,
        },
    )
    assert resp_missing.status_code == 201
    data_missing = resp_missing.json()
    assert data_missing["passed_tasks"] == 0
    assert data_missing["pass_rate"] == 0.0
    t_missing = client.get(
        f"/api/v1/sandboxes/runs/{data_missing['id']}/transcripts/task_1",
        headers=env["viewer_a"],
    ).json()
    assert t_missing["status"] == "failed"
    assert t_missing["score"] == 0.0
    assert "expected_output" in (t_missing["error"] or "")

    # With matching expected_output, llm_judge evaluates against output
    resp_ok = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "llm-judge-with-expected",
            "runner_backend": "fake",
            "solver_config": {"steps": [{"tool_name": "bash", "tool_args": {"cmd": "echo 42"}}]},
            "scorer_config": {"name": "llm_judge", "expected_output": "[simulated:bash]"},
            "sync_exec": True,
        },
    )
    assert resp_ok.status_code == 201
    assert resp_ok.json()["pass_rate"] == 1.0


def test_artifact_store_failures_mark_unverified_and_fail_task(env, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("MATRIX_ENV", "test")
    client: TestClient = env["client"]
    app = env["app"]

    # 1. save_blob failure inside evaluate_scorer
    sb = FakeSandbox(workdir=tmp_path / "sb_art_fail")
    sb.write_file("out.txt", "hello")

    def _broken_save(_rel: str, _data: bytes) -> str | None:
        raise RuntimeError("S3 quota exceeded")

    with caplog.at_level(logging.WARNING):
        status, score, details, arts, err = evaluate_scorer(
            sandbox=sb,
            task=SandboxTask(task_key="t_art", expected_files=["out.txt"], artifacts=["out.txt"]),
            scorer_config={"name": "file_exists", "files": ["out.txt"]},
            steps=[],
            save_blob=_broken_save,
        )
    assert status == "failed"
    assert score == 0.0
    assert details.get("artifact_verification_failed") is True
    assert len(arts) == 1
    assert arts[0]["verified"] is False
    assert arts[0]["storage_uri"] is None
    assert "S3 quota exceeded" in (arts[0].get("error") or "")
    assert "S3 quota exceeded" in (err or "")
    assert any(r.levelno >= logging.WARNING and "S3 quota exceeded" in r.message for r in caplog.records)

    # 2. get_artifact_store() failure inside run_sandbox_job (no debug-only suppression)
    caplog.clear()
    create_res = client.post(
        "/api/v1/sandboxes/runs",
        headers=env["member_a"],
        json={
            "name": "broken-default-store-run",
            "runner_backend": "fake",
            "solver_config": {
                "steps": [
                    {"tool_name": "file_edit", "tool_args": {"path": "result.txt", "content": "done"}}
                ]
            },
            "scorer_config": {"name": "file_exists", "files": ["result.txt"], "artifacts": ["result.txt"]},
            "sync_exec": False,
        },
    )
    assert create_res.status_code == 201
    run_id = create_res.json()["id"]

    import matrix_server.workers.artifact_handlers as artifact_handlers_mod

    monkeypatch.setattr(
        artifact_handlers_mod,
        "get_artifact_store",
        lambda: (_ for _ in ()).throw(RuntimeError("S3 endpoint unreachable")),
    )

    with caplog.at_level(logging.WARNING):
        run_sandbox_job(app.state.session_factory, run_id, artifact_store=None)

    assert any(
        r.levelno >= logging.ERROR and "S3 endpoint unreachable" in r.message
        for r in caplog.records
    )

    detail = client.get(f"/api/v1/sandboxes/runs/{run_id}/transcripts/task_1", headers=env["viewer_a"]).json()
    assert detail["status"] == "failed"
    assert detail["score"] == 0.0
    assert len(detail["verified_artifacts"]) == 1
    assert detail["verified_artifacts"][0]["verified"] is False
    assert detail["verified_artifacts"][0]["storage_uri"] is None
    assert "S3 endpoint unreachable" in (detail["verified_artifacts"][0].get("error") or "")
    with app.state.session_factory() as db:
        db_run = db.get(SandboxRun, run_id)
        assert db_run is not None and db_run.passed_tasks == 0 and db_run.pass_rate == 0.0
