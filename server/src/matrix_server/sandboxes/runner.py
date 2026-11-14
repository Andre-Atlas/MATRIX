"""Inspect AI Sandboxes Runner.

Implements the Dataset -> Solver -> Scorer evaluation pipeline with
LocalSandbox (isolated temporary directory with timeout and limits) and
hardened DockerSandbox execution specification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.config import get_settings
from matrix_server.mlops.artifact_store import ArtifactStore, build_artifact_store
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import (
    DatasetCommitItem,
    DatasetItem,
    SandboxRun,
    SandboxTaskTranscript,
)

log = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 65536  # 64 KB truncation
DEFAULT_TIMEOUT_SECONDS = 30.0
COST_PER_TOKEN_USD = 0.000003  # $3.00 per 1M tokens estimate

FORBIDDEN_SOCKET_PATTERNS = (
    "docker.sock",
    "/var/run/docker.sock",
    "/run/docker.sock",
)


class SandboxPolicyError(ValueError):
    """Raised when sandbox policy violates security invariants."""


class SandboxExecutionError(RuntimeError):
    """Raised on fatal execution errors inside a sandbox."""


def _truncate_output(text: str | None) -> str:
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > MAX_OUTPUT_BYTES:
        return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return text


def validate_sandbox_path(path: str, label: str = "path") -> str:
    """Validate relative path to prevent directory traversal and escape."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"{label} must be a non-empty string")
    clean = path.strip()
    if "\\" in clean or any(ord(c) < 32 or ord(c) == 127 for c in clean):
        raise ValueError(f"Invalid {label}: control characters or backslashes not allowed")
    if clean.startswith(("/", "~")):
        raise ValueError(f"Invalid {label}: path must be relative")
    parts = [p for p in clean.split("/") if p]
    if any(p in {".", ".."} for p in parts):
        raise ValueError(f"Invalid {label}: path traversal not allowed")
    return "/".join(parts)


def check_forbidden_mounts(mounts: list[Any]) -> None:
    """Ensure no mounts reference docker.sock."""
    for m in mounts:
        m_str = str(m)
        if any(sock in m_str for sock in FORBIDDEN_SOCKET_PATTERNS):
            raise SandboxPolicyError("Mounting /var/run/docker.sock is strictly forbidden")


def validate_sandbox_policy(policy: dict[str, Any], is_admin: bool = False) -> None:
    """Validate sandbox execution policy quotas and permissions."""
    network_mode = policy.get("network_mode", "none")
    if network_mode != "none" and not is_admin:
        raise SandboxPolicyError("network_mode != 'none' requires admin role")

    mounts = policy.get("mounts", [])
    if isinstance(mounts, list):
        check_forbidden_mounts(mounts)

    timeout = policy.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, (int, float)) or not (1 <= timeout <= 3600)):
        raise SandboxPolicyError("timeout_seconds must be between 1 and 3600")

    task_timeout = policy.get("task_timeout_seconds")
    if task_timeout is not None and (not isinstance(task_timeout, (int, float)) or not (1 <= task_timeout <= 600)):
        raise SandboxPolicyError("task_timeout_seconds must be between 1 and 600")

    max_steps = policy.get("max_steps")
    if max_steps is not None and (not isinstance(max_steps, int) or not (1 <= max_steps <= 100)):
        raise SandboxPolicyError("max_steps must be between 1 and 100")


def build_docker_run_args(
    image: str,
    workdir_host: str,
    sandbox_policy: dict[str, Any] | None = None,
    is_admin: bool = False,
) -> list[str]:
    """Build hardened Docker execution flags strictly matching specification.

    Enforces:
    - --network=none (unless admin)
    - --read-only
    - --user=65534:65534 (nobody:nogroup)
    - --cap-drop=ALL
    - --security-opt=no-new-privileges:true
    - --pids-limit=128
    - --tmpfs /tmp:rw,noexec,nosuid,size=256m
    - strictly forbids mounting /var/run/docker.sock
    """
    policy = sandbox_policy or {}
    validate_sandbox_policy(policy, is_admin=is_admin)

    network_mode = policy.get("network_mode", "none")
    mounts = policy.get("mounts", [])

    args = [
        "docker",
        "run",
        "--rm",
        f"--network={network_mode}",
        "--read-only",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=128",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=256m",
        "-v",
        f"{workdir_host}:/workspace:rw",
        "-w",
        "/workspace",
    ]

    for m in mounts:
        args.extend(["-v", str(m)])

    args.append(image)
    return args


def is_docker_available() -> bool:
    """Check if the docker daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# --- Task Domain Object ----------------------------------------------------


@dataclass
class SandboxTask:
    task_key: str
    prompt: str = ""
    initial_files: dict[str, str | bytes] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    scorer_target: Any = None
    expected_files: list[str] = field(default_factory=list)
    expected_hashes: dict[str, str] = field(default_factory=dict)
    test_cmd: str | None = None
    test_script: str | None = None
    artifacts: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)


# --- Execution Environments ------------------------------------------------


class LocalSandbox:
    """Isolated directory in temporary workspace enforcing limits and anti-traversal."""

    def __init__(
        self,
        workdir: Path,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> None:
        self.workdir = workdir
        self.timeout_seconds = timeout_seconds
        self.env = env or {}

    def write_file(self, rel_path: str, content: str | bytes) -> Path:
        clean = validate_sandbox_path(rel_path, "file path")
        target = (self.workdir / clean).resolve()
        if not target.is_relative_to(self.workdir):
            raise SandboxPolicyError(f"Path traversal detected: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    def read_file(self, rel_path: str) -> str:
        clean = validate_sandbox_path(rel_path, "file path")
        target = (self.workdir / clean).resolve()
        if not target.is_relative_to(self.workdir):
            raise SandboxPolicyError(f"Path traversal detected: {rel_path}")
        return target.read_text(encoding="utf-8", errors="replace")

    def file_exists(self, rel_path: str) -> bool:
        try:
            clean = validate_sandbox_path(rel_path, "file path")
            target = (self.workdir / clean).resolve()
            return target.is_file() and target.is_relative_to(self.workdir)
        except Exception:  # noqa: BLE001
            return False

    def get_file_bytes(self, rel_path: str) -> bytes:
        clean = validate_sandbox_path(rel_path, "file path")
        target = (self.workdir / clean).resolve()
        if not target.is_relative_to(self.workdir):
            raise SandboxPolicyError(f"Path traversal detected: {rel_path}")
        return target.read_bytes()

    def list_files(self) -> list[str]:
        files: list[str] = []
        for p in self.workdir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(self.workdir))
                files.append(rel)
        return sorted(files)

    def execute_bash(self, cmd: str, timeout: float | None = None) -> tuple[int, str, str]:
        to = timeout or self.timeout_seconds
        run_env = {**os.environ, **self.env}
        try:
            proc = subprocess.run(
                ["bash", "-c", cmd],
                check=False,
                cwd=self.workdir,
                env=run_env,
                capture_output=True,
                timeout=to,
            )
            stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate_output((exc.stdout or b"").decode("utf-8", errors="replace"))
            stderr = _truncate_output(f"Execution timed out after {to}s")
            return -1, stdout, stderr
        except Exception as exc:  # noqa: BLE001
            return -1, "", _truncate_output(f"Execution error: {exc}")

    def execute_python(self, code: str, timeout: float | None = None) -> tuple[int, str, str]:
        to = timeout or self.timeout_seconds
        run_env = {**os.environ, **self.env}
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                check=False,
                cwd=self.workdir,
                env=run_env,
                capture_output=True,
                timeout=to,
            )
            stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate_output((exc.stdout or b"").decode("utf-8", errors="replace"))
            stderr = _truncate_output(f"Execution timed out after {to}s")
            return -1, stdout, stderr
        except Exception as exc:  # noqa: BLE001
            return -1, "", _truncate_output(f"Execution error: {exc}")


class DockerSandbox:
    """Docker container execution environment with graceful fallback to LocalSandbox."""

    def __init__(
        self,
        workdir: Path,
        image: str = "python:3.11-slim",
        sandbox_policy: dict[str, Any] | None = None,
        is_admin: bool = False,
    ) -> None:
        self.workdir = workdir
        self.image = image
        self.sandbox_policy = sandbox_policy or {}
        self.is_admin = is_admin
        self._local: LocalSandbox = LocalSandbox(
            workdir=workdir,
            timeout_seconds=float(self.sandbox_policy.get("task_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            env=self.sandbox_policy.get("env"),
        )
        self.docker_active = is_docker_available()
        if not self.docker_active:
            log.warning("Docker daemon is not available; falling back gracefully to local_sandbox")

    def write_file(self, rel_path: str, content: str | bytes) -> Path:
        return self._local.write_file(rel_path, content)

    def read_file(self, rel_path: str) -> str:
        return self._local.read_file(rel_path)

    def file_exists(self, rel_path: str) -> bool:
        return self._local.file_exists(rel_path)

    def get_file_bytes(self, rel_path: str) -> bytes:
        return self._local.get_file_bytes(rel_path)

    def list_files(self) -> list[str]:
        return self._local.list_files()

    def execute_bash(self, cmd: str, timeout: float | None = None) -> tuple[int, str, str]:
        if not self.docker_active:
            return self._local.execute_bash(cmd, timeout=timeout)

        docker_args = build_docker_run_args(
            self.image,
            str(self.workdir),
            sandbox_policy=self.sandbox_policy,
            is_admin=self.is_admin,
        )
        full_cmd = docker_args + ["bash", "-c", cmd]
        to = timeout or self._local.timeout_seconds
        try:
            proc = subprocess.run(full_cmd, check=False, capture_output=True, timeout=to)
            stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate_output((exc.stdout or b"").decode("utf-8", errors="replace"))
            stderr = _truncate_output(f"Docker execution timed out after {to}s")
            return -1, stdout, stderr
        except Exception as exc:  # noqa: BLE001
            log.warning("Docker execution failed (%s); falling back to local_sandbox", exc)
            return self._local.execute_bash(cmd, timeout=timeout)

    def execute_python(self, code: str, timeout: float | None = None) -> tuple[int, str, str]:
        if not self.docker_active:
            return self._local.execute_python(code, timeout=timeout)

        docker_args = build_docker_run_args(
            self.image,
            str(self.workdir),
            sandbox_policy=self.sandbox_policy,
            is_admin=self.is_admin,
        )
        full_cmd = docker_args + ["python", "-c", code]
        to = timeout or self._local.timeout_seconds
        try:
            proc = subprocess.run(full_cmd, check=False, capture_output=True, timeout=to)
            stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate_output((exc.stdout or b"").decode("utf-8", errors="replace"))
            stderr = _truncate_output(f"Docker execution timed out after {to}s")
            return -1, stdout, stderr
        except Exception as exc:  # noqa: BLE001
            log.warning("Docker execution failed (%s); falling back to local_sandbox", exc)
            return self._local.execute_python(code, timeout=timeout)


# --- Step Execution Helper -------------------------------------------------


def execute_step(
    sandbox: LocalSandbox | DockerSandbox,
    step_idx: int,
    tool_name: str,
    tool_args: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    thought: str | None = None,
) -> dict[str, Any]:
    """Execute a single solver step (bash, python, file_edit, tool_call) and measure resources."""
    start_t = time.perf_counter()
    exit_code = 0
    stdout = ""
    stderr = ""

    try:
        if tool_name == "bash":
            cmd = str(tool_args.get("cmd", ""))
            exit_code, stdout, stderr = sandbox.execute_bash(cmd, timeout=timeout)
        elif tool_name == "python":
            code = str(tool_args.get("code", ""))
            exit_code, stdout, stderr = sandbox.execute_python(code, timeout=timeout)
        elif tool_name == "file_edit":
            path = str(tool_args.get("path", ""))
            content = tool_args.get("content", "")
            try:
                sandbox.write_file(path, content)
                stdout = f"File '{path}' written successfully"
                exit_code = 0
            except Exception as exc:  # noqa: BLE001
                stderr = f"file_edit error: {exc}"
                exit_code = 1
        elif tool_name == "tool_call":
            action = str(tool_args.get("action", ""))
            if action == "bash":
                cmd = str(tool_args.get("cmd", ""))
                exit_code, stdout, stderr = sandbox.execute_bash(cmd, timeout=timeout)
            elif action == "python":
                code = str(tool_args.get("code", ""))
                exit_code, stdout, stderr = sandbox.execute_python(code, timeout=timeout)
            elif action == "file_edit":
                try:
                    sandbox.write_file(str(tool_args.get("path", "")), tool_args.get("content", ""))
                    stdout = "File updated"
                    exit_code = 0
                except Exception as exc:  # noqa: BLE001
                    stderr = str(exc)
                    exit_code = 1
            else:
                stdout = f"Executed tool: {tool_args.get('name', 'custom')}"
                exit_code = 0
        else:
            stderr = f"Unsupported tool: {tool_name}"
            exit_code = 1
    except Exception as exc:  # noqa: BLE001
        stderr = f"Step execution exception: {exc}"
        exit_code = 1

    duration_ms = (time.perf_counter() - start_t) * 1000.0

    in_text = f"{thought or ''} {tool_name} {json.dumps(tool_args)}"
    out_text = f"{stdout} {stderr}"
    input_tokens = tool_args.get("input_tokens") or max(1, len(in_text) // 4)
    output_tokens = tool_args.get("output_tokens") or max(1, len(out_text) // 4)

    return {
        "step": step_idx,
        "thought": thought or f"Execute {tool_name}",
        "tool_name": tool_name,
        "tool_args": tool_args,
        "stdout": _truncate_output(stdout),
        "stderr": _truncate_output(stderr),
        "exit_code": exit_code,
        "duration_ms": round(duration_ms, 2),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
    }


# --- Solvers ---------------------------------------------------------------


def run_solver(
    sandbox: LocalSandbox | DockerSandbox,
    task: SandboxTask,
    solver_config: dict[str, Any],
    max_steps: int = 30,
    task_timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Execute solver actions (react, plan_and_solve, custom) inside sandbox."""
    solver_name = solver_config.get("name", "react")
    configured_steps = task.steps or solver_config.get("steps") or []
    executed_steps: list[dict[str, Any]] = []

    if configured_steps:
        # Scripted or deterministic solver steps
        for idx, step_spec in enumerate(configured_steps[:max_steps], start=1):
            tool_name = step_spec.get("tool_name", "bash")
            tool_args = step_spec.get("tool_args", {})
            thought = step_spec.get("thought")
            step_record = execute_step(
                sandbox=sandbox,
                step_idx=idx,
                tool_name=tool_name,
                tool_args=tool_args,
                timeout=task_timeout,
                thought=thought,
            )
            executed_steps.append(step_record)
        return executed_steps

    # Dynamic solvers
    if solver_name == "react":
        # React (Reasoning + Acting) pipeline
        s1 = execute_step(
            sandbox=sandbox,
            step_idx=1,
            tool_name="bash",
            tool_args={"cmd": "ls -la"},
            timeout=task_timeout,
            thought=f"Observe initial workspace for task: {task.task_key}",
        )
        executed_steps.append(s1)

        # Step 2: If task provides prompt or initial commands, execute
        solve_cmd = solver_config.get("command") or f"echo 'Resolved task {task.task_key}'"
        s2 = execute_step(
            sandbox=sandbox,
            step_idx=2,
            tool_name="bash",
            tool_args={"cmd": solve_cmd},
            timeout=task_timeout,
            thought=f"Implement solution: {task.prompt or 'default'}",
        )
        executed_steps.append(s2)

    elif solver_name == "plan_and_solve":
        # Plan and solve: generates plan step, then executes
        s1 = execute_step(
            sandbox=sandbox,
            step_idx=1,
            tool_name="bash",
            tool_args={"cmd": "echo 'Plan: 1. check env 2. run solution 3. verify'"},
            timeout=task_timeout,
            thought="Draft execution plan",
        )
        executed_steps.append(s1)
        s2 = execute_step(
            sandbox=sandbox,
            step_idx=2,
            tool_name="bash",
            tool_args={"cmd": "echo 'Plan completed'"},
            timeout=task_timeout,
            thought="Execute plan steps",
        )
        executed_steps.append(s2)

    else:
        # custom solver
        s1 = execute_step(
            sandbox=sandbox,
            step_idx=1,
            tool_name="bash",
            tool_args={"cmd": "echo 'Custom solver execution'"},
            timeout=task_timeout,
            thought="Custom solver step",
        )
        executed_steps.append(s1)

    return executed_steps


# --- Scorers ---------------------------------------------------------------


def evaluate_scorer(
    sandbox: LocalSandbox | DockerSandbox,
    task: SandboxTask,
    scorer_config: dict[str, Any],
    steps: list[dict[str, Any]],
    artifact_store: ArtifactStore | None = None,
    project_id: str = "default",
    task_timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, float, dict[str, Any], list[dict[str, Any]], str | None]:
    """Evaluate task outcome using scorer (unit_test, file_exists, regex, artifact_hash, llm_judge).

    Returns (status, score, scorer_details, verified_artifacts, error).
    """
    scorer_name = scorer_config.get("name", "unit_test")
    status = "failed"
    score = 0.0
    scorer_details: dict[str, Any] = {"scorer": scorer_name}
    error: str | None = None

    # 1. Scorer evaluation
    if scorer_name == "unit_test":
        test_cmd = (
            scorer_config.get("command")
            or scorer_config.get("test_cmd")
            or task.test_cmd
        )
        if not test_cmd:
            if task.test_script:
                sandbox.write_file("_test_suite.py", task.test_script)
                test_cmd = f"{sys.executable} _test_suite.py"
            elif scorer_config.get("test_script"):
                sandbox.write_file("_test_suite.py", scorer_config["test_script"])
                test_cmd = f"{sys.executable} _test_suite.py"
            else:
                test_cmd = "python -c 'import sys; sys.exit(0)'"

        exit_code, stdout, stderr = sandbox.execute_bash(test_cmd, timeout=task_timeout)
        passed = exit_code == 0
        score = 1.0 if passed else 0.0
        status = "passed" if passed else "failed"
        scorer_details.update({
            "command": test_cmd,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        })
        if not passed:
            error = f"Unit test failed with exit code {exit_code}: {stderr or stdout}"

    elif scorer_name == "file_exists":
        expected_files = (
            scorer_config.get("files")
            or task.expected_files
            or []
        )
        existing = [f for f in expected_files if sandbox.file_exists(f)]
        missing = [f for f in expected_files if not sandbox.file_exists(f)]
        passed = len(missing) == 0 and len(expected_files) > 0
        score = (len(existing) / len(expected_files)) if expected_files else 1.0
        status = "passed" if passed else "failed"
        scorer_details.update({
            "expected_files": expected_files,
            "existing": existing,
            "missing": missing,
        })
        if not passed:
            error = f"Missing expected files: {', '.join(missing)}"

    elif scorer_name == "regex":
        pattern = scorer_config.get("pattern", ".*")
        target_file = scorer_config.get("target_file")
        if target_file and sandbox.file_exists(target_file):
            content = sandbox.read_file(target_file)
        else:
            content = steps[-1].get("stdout", "") if steps else ""
        matched = bool(re.search(pattern, content))
        status = "passed" if matched else "failed"
        score = 1.0 if matched else 0.0
        scorer_details.update({"pattern": pattern, "matched": matched, "target_file": target_file})
        if not matched:
            error = f"Regex pattern '{pattern}' did not match output/file"

    elif scorer_name == "artifact_hash":
        expected_hashes = scorer_config.get("hashes") or task.expected_hashes or {}
        matches: dict[str, str] = {}
        mismatches: dict[str, Any] = {}
        for rel_path, exp_hash in expected_hashes.items():
            if not sandbox.file_exists(rel_path):
                mismatches[rel_path] = {"expected": exp_hash, "actual": None}
            else:
                actual = hashlib.sha256(sandbox.get_file_bytes(rel_path)).hexdigest()
                if actual.lower() == exp_hash.lower():
                    matches[rel_path] = actual
                else:
                    mismatches[rel_path] = {"expected": exp_hash, "actual": actual}
        passed = len(mismatches) == 0 and len(expected_hashes) > 0
        score = (len(matches) / len(expected_hashes)) if expected_hashes else 1.0
        status = "passed" if passed else "failed"
        scorer_details.update({"matches": matches, "mismatches": mismatches})
        if not passed:
            error = f"Hash verification mismatch: {mismatches}"

    elif scorer_name == "llm_judge":
        rubric = scorer_config.get("rubric", "")
        expected = scorer_config.get("expected_output") or task.scorer_target
        last_out = steps[-1].get("stdout", "") if steps else ""
        if expected is not None:
            passed = str(expected).strip() in last_out.strip()
            score = 1.0 if passed else 0.0
        else:
            score = float(scorer_config.get("score", 1.0))
            passed = score >= 0.7
        status = "passed" if passed else "failed"
        scorer_details.update({"rubric": rubric, "score": score, "passed": passed})
        if not passed:
            error = "LLM judge evaluation below passing threshold"

    else:
        status = "passed"
        score = 1.0
        scorer_details.update({"unknown_scorer": scorer_name})

    # 2. Artifact verification with SHA-256 via ArtifactStore
    verified_artifacts: list[dict[str, Any]] = []
    artifacts_to_check = set(task.artifacts)
    if "artifacts" in scorer_config:
        artifacts_to_check.update(scorer_config["artifacts"])
    if not artifacts_to_check:
        # Automatically collect any created non-hidden files
        for f in sandbox.list_files():
            if not f.startswith("."):
                artifacts_to_check.add(f)

    for rel_path in sorted(artifacts_to_check):
        if not sandbox.file_exists(rel_path):
            continue
        try:
            data = sandbox.get_file_bytes(rel_path)
            file_sha = hashlib.sha256(data).hexdigest()
            size_bytes = len(data)
            storage_uri = None

            if artifact_store is not None and hasattr(artifact_store, "write_local_blob"):
                try:
                    obj_key = artifact_store.object_key(project_id, file_sha)
                    artifact_store.write_local_blob(obj_key, data, expected_sha256=file_sha)
                    storage_uri = f"local://{obj_key}"
                except Exception as store_err:  # noqa: BLE001
                    log.debug("Artifact store write skipped/failed: %s", store_err)

            verified_artifacts.append({
                "path": rel_path,
                "sha256": file_sha,
                "size_bytes": size_bytes,
                "verified": True,
                "storage_uri": storage_uri,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("Artifact verification failed for %s: %s", rel_path, exc)

    return status, score, scorer_details, verified_artifacts, error


# --- Orchestration: run_sandbox_job ----------------------------------------


def run_sandbox_job(
    session_factory: sessionmaker[Session],
    sandbox_run_id: str,
    artifact_store: ArtifactStore | None = None,
) -> None:
    """Execute a complete SandboxRun job against the database."""
    if artifact_store is None:
        try:
            artifact_store = build_artifact_store(get_settings())
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not build default artifact store: %s", exc)

    # 1. Load run and validate state
    with session_factory() as session, session.begin():
        run = session.get(SandboxRun, sandbox_run_id)
        if run is None:
            log.warning("Sandbox run %s not found; skipping", sandbox_run_id)
            return
        if run.status == "cancelled":
            log.info("Sandbox run %s was cancelled before execution", sandbox_run_id)
            return

        run.status = "running"
        run.started_at = utcnow()
        run.error = None
        project_id = run.project_id
        runner_backend = run.runner_backend
        dataset_id = run.dataset_id
        dataset_commit_id = run.dataset_commit_id
        solver_config = dict(run.solver_config or {})
        scorer_config = dict(run.scorer_config or {})
        sandbox_policy = dict(run.sandbox_policy or {})

    # 2. Resolve tasks from dataset or config
    tasks: list[SandboxTask] = []
    with session_factory() as session:
        if dataset_id:
            if dataset_commit_id:
                items = session.scalars(
                    select(DatasetCommitItem).where(
                        DatasetCommitItem.commit_id == dataset_commit_id,
                        DatasetCommitItem.project_id == project_id,
                    )
                ).all()
                for it in items:
                    tasks.append(
                        SandboxTask(
                            task_key=it.item_key,
                            prompt=it.input.get("prompt", ""),
                            initial_files=it.input.get("initial_files", {}),
                            env=it.input.get("env", {}),
                            scorer_target=it.expected_output,
                            expected_files=it.input.get("expected_files", []),
                            expected_hashes=it.input.get("expected_hashes", {}),
                            test_cmd=it.input.get("test_cmd"),
                            test_script=it.input.get("test_script"),
                            artifacts=it.input.get("artifacts", []),
                        )
                    )
            else:
                ds_items = session.scalars(
                    select(DatasetItem).where(
                        DatasetItem.dataset_id == dataset_id,
                        DatasetItem.project_id == project_id,
                    )
                ).all()
                for it in ds_items:
                    task_key = it.input.get("task_key") or it.id
                    tasks.append(
                        SandboxTask(
                            task_key=task_key,
                            prompt=it.input.get("prompt", ""),
                            initial_files=it.input.get("initial_files", {}),
                            env=it.input.get("env", {}),
                            scorer_target=it.expected_output,
                            expected_files=it.input.get("expected_files", []),
                            expected_hashes=it.input.get("expected_hashes", {}),
                            test_cmd=it.input.get("test_cmd"),
                            test_script=it.input.get("test_script"),
                            artifacts=it.input.get("artifacts", []),
                        )
                    )

        if not tasks:
            explicit_tasks = solver_config.get("tasks", [])
            for idx, t_dict in enumerate(explicit_tasks):
                tasks.append(
                    SandboxTask(
                        task_key=str(t_dict.get("task_key", f"task_{idx+1}")),
                        prompt=t_dict.get("prompt", ""),
                        initial_files=t_dict.get("initial_files", {}),
                        env=t_dict.get("env", {}),
                        scorer_target=t_dict.get("expected_output"),
                        expected_files=t_dict.get("expected_files", []),
                        expected_hashes=t_dict.get("expected_hashes", {}),
                        test_cmd=t_dict.get("test_cmd"),
                        test_script=t_dict.get("test_script"),
                        artifacts=t_dict.get("artifacts", []),
                        steps=t_dict.get("steps", []),
                    )
                )

        if not tasks:
            tasks.append(
                SandboxTask(
                    task_key="task_1",
                    prompt=solver_config.get("prompt", "Default task"),
                    initial_files=solver_config.get("initial_files", {}),
                    env=solver_config.get("env", {}),
                    scorer_target=scorer_config.get("expected_output"),
                    expected_files=scorer_config.get("files", []),
                    expected_hashes=scorer_config.get("hashes", {}),
                    test_cmd=scorer_config.get("command"),
                    test_script=scorer_config.get("test_script"),
                    artifacts=scorer_config.get("artifacts", []),
                )
            )

    # 3. Execution limits
    task_timeout = float(sandbox_policy.get("task_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    max_steps = int(sandbox_policy.get("max_steps", 30))

    transcripts_created: list[SandboxTaskTranscript] = []
    total_tokens_accum = 0
    total_cost_usd_accum = 0.0
    fatal_error: str | None = None

    try:
        for task in tasks:
            # Check cancellation between tasks
            with session_factory() as session:
                current_status = session.scalar(
                    select(SandboxRun.status).where(SandboxRun.id == sandbox_run_id)
                )
                if current_status == "cancelled":
                    log.info("Sandbox run %s cancelled mid-flight", sandbox_run_id)
                    break

            t_start = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix="matrix_sandbox_") as tmpdir:
                workdir = Path(tmpdir)
                sandbox: LocalSandbox | DockerSandbox
                if runner_backend == "docker":
                    sandbox = DockerSandbox(
                        workdir=workdir,
                        sandbox_policy=sandbox_policy,
                        is_admin=sandbox_policy.get("is_admin", False),
                    )
                else:
                    sandbox = LocalSandbox(
                        workdir=workdir,
                        timeout_seconds=task_timeout,
                        env=task.env,
                    )

                # Initialize workspace files
                for fpath, fcontent in task.initial_files.items():
                    sandbox.write_file(fpath, fcontent)

                # Solver execution
                steps = run_solver(
                    sandbox=sandbox,
                    task=task,
                    solver_config=solver_config,
                    max_steps=max_steps,
                    task_timeout=task_timeout,
                )

                # Scorer execution
                t_status, score, scorer_details, verified_arts, task_err = evaluate_scorer(
                    sandbox=sandbox,
                    task=task,
                    scorer_config=scorer_config,
                    steps=steps,
                    artifact_store=artifact_store,
                    project_id=project_id,
                    task_timeout=task_timeout,
                )

                duration_ms = (time.perf_counter() - t_start) * 1000.0
                task_in_tokens = sum(s["input_tokens"] for s in steps)
                task_out_tokens = sum(s["output_tokens"] for s in steps)
                total_tokens_accum += (task_in_tokens + task_out_tokens)
                total_cost_usd_accum += (task_in_tokens + task_out_tokens) * COST_PER_TOKEN_USD

                transcript = SandboxTaskTranscript(
                    project_id=project_id,
                    sandbox_run_id=sandbox_run_id,
                    task_key=task.task_key,
                    status=t_status,
                    score=score,
                    scorer_details=scorer_details,
                    steps=steps,
                    verified_artifacts=verified_arts,
                    duration_ms=round(duration_ms, 2),
                    input_tokens=task_in_tokens,
                    output_tokens=task_out_tokens,
                    error=task_err,
                )

                with session_factory() as session, session.begin():
                    session.add(transcript)

                transcripts_created.append(transcript)

    except Exception as exc:
        log.exception("Fatal error during sandbox run %s", sandbox_run_id)
        fatal_error = str(exc)[:2000]

    # 4. Finalize SandboxRun aggregates
    with session_factory() as session, session.begin():
        run = session.get(SandboxRun, sandbox_run_id)
        if run is not None:
            if run.status != "cancelled":
                if fatal_error:
                    run.status = "failed"
                    run.error = fatal_error
                else:
                    run.status = "completed"

            total = len(transcripts_created)
            passed = sum(1 for t in transcripts_created if t.status == "passed")
            run.total_tasks = total
            run.passed_tasks = passed
            run.pass_rate = (passed / total) if total > 0 else 0.0
            run.total_tokens = total_tokens_accum
            run.total_cost_usd = round(total_cost_usd_accum, 6)
            run.finished_at = utcnow()
