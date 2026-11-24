"""Inspect AI Sandboxes Runner (ADR-010, T7).

Implements the Dataset -> Solver -> Scorer evaluation pipeline with:
- `SandboxEngine` interface (`docker` and `fake`; Inspect AI is the planned real engine per ADR-010).
- `DockerSandbox`: hardened, fail-closed container execution (never falls back to host execution).
- `FakeSandbox`: deterministic simulated execution allowed ONLY when `MATRIX_ENV` is `test` or `dev`.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.artifacts import ArtifactStore
from matrix_server.artifacts.service import Limits, store_bytes
from matrix_server.config import get_settings
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import (
    DatasetCommitItem,
    DatasetItem,
    SandboxRun,
    SandboxTaskTranscript,
)

log = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 65536  # 64 KiB truncation
DEFAULT_TIMEOUT_SECONDS = 30.0
COST_PER_TOKEN_USD = 0.000003  # $3.00 per 1M tokens estimate
DEFAULT_SANDBOX_IMAGE = "python:3.12-slim"
MAX_CPUS = 4.0

NETWORK_UNSUPPORTED_MSG = "network access for sandboxes is not supported until an egress proxy exists"
RESERVED_POLICY_KEYS = frozenset({"network_approved_by", "is_admin"})
VALID_SCORERS = frozenset({"unit_test", "file_exists", "regex", "artifact_hash", "llm_judge"})
_MEMORY_RE = re.compile(r"^[1-9][0-9]*[kmgKMG]$")
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_DAEMON_ERROR_MARKERS = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "error during connect",
    "connect: no such file",
    "connect: connection refused",
    "permission denied while trying to connect",
)


class SandboxPolicyError(ValueError):
    """Raised when sandbox policy violates security invariants."""


class SandboxExecutionError(RuntimeError):
    """Raised on fatal execution errors inside a sandbox."""


class SandboxUnavailableError(RuntimeError):
    """Raised when the Docker daemon/CLI is unavailable (fail-closed, never host fallback)."""


def is_fake_backend_allowed() -> bool:
    """`fake` sandbox backend is allowed ONLY when MATRIX_ENV is explicitly `test` or `dev`."""
    return os.environ.get("MATRIX_ENV", "").strip().lower() in {"test", "dev"}


def is_test_env() -> bool:
    """`sync_exec` is allowed only in test environments (`MATRIX_ENV=test` or pytest)."""
    env = os.environ.get("MATRIX_ENV")
    if env is not None:
        return env.strip().lower() == "test"
    return "PYTEST_CURRENT_TEST" in os.environ


def allowed_sandbox_images() -> list[str]:
    """Return the operator-configured image allowlist (`MATRIX_SANDBOX_IMAGES`)."""
    raw = os.environ.get("MATRIX_SANDBOX_IMAGES")
    if raw is None:
        raw = get_settings().sandbox_images
    return [img.strip() for img in raw.split(",") if img.strip()]


def default_sandbox_image() -> str:
    images = allowed_sandbox_images()
    return images[0] if images else DEFAULT_SANDBOX_IMAGE


def _docker_cli_env() -> dict[str, str]:
    """Minimal environment for the `docker` CLI — never leaks server secrets."""
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "",
    }
    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


def _safe_name(value: str, fallback: str = "x", max_len: int = 16) -> str:
    cleaned = _SAFE_NAME_RE.sub("-", str(value or "")).strip("-")
    return (cleaned[:max_len] or fallback).lower()


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


def validate_sandbox_policy(
    policy: dict[str, Any],
    *,
    image: str | None = None,
    allowed_images: list[str] | None = None,
) -> dict[str, Any]:
    """Validate sandbox execution policy and return normalized container limits."""
    reserved = sorted(k for k in policy if k in RESERVED_POLICY_KEYS)
    if reserved:
        raise SandboxPolicyError(f"sandbox_policy keys are server-owned: {', '.join(reserved)}")

    network_mode = policy.get("network_mode", "none")
    if network_mode != "none":
        raise SandboxPolicyError(NETWORK_UNSUPPORTED_MSG)

    if "mounts" in policy or "volumes" in policy:
        raise SandboxPolicyError("User-specified mounts/volumes are not allowed in sandbox_policy")

    settings = get_settings()
    allowlist = allowed_images if allowed_images is not None else allowed_sandbox_images()
    effective_image = image if image is not None else policy.get("image") or (
        allowlist[0] if allowlist else DEFAULT_SANDBOX_IMAGE
    )
    if not isinstance(effective_image, str) or not effective_image or effective_image not in allowlist:
        raise SandboxPolicyError(
            f"Sandbox image '{effective_image}' is not in the operator allowlist"
        )

    pids_limit = policy.get("pids_limit", settings.sandbox_pids_limit)
    if isinstance(pids_limit, bool) or not isinstance(pids_limit, int) or not (1 <= pids_limit <= 4096):
        raise SandboxPolicyError("pids_limit must be an integer between 1 and 4096")

    memory = policy.get("memory", settings.sandbox_memory)
    if not isinstance(memory, str) or not _MEMORY_RE.match(memory):
        raise SandboxPolicyError("memory must be a valid size string such as '512m' or '1g'")

    cpus = policy.get("cpus", settings.sandbox_cpus)
    if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or not (0 < float(cpus) <= MAX_CPUS):
        raise SandboxPolicyError(f"cpus must be greater than 0 and at most {int(MAX_CPUS)}")

    timeout = policy.get("timeout_seconds")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not (1 <= timeout <= 3600)
    ):
        raise SandboxPolicyError("timeout_seconds must be between 1 and 3600")

    task_timeout = policy.get("task_timeout_seconds")
    if task_timeout is not None and (
        isinstance(task_timeout, bool)
        or not isinstance(task_timeout, (int, float))
        or not (1 <= task_timeout <= 600)
    ):
        raise SandboxPolicyError("task_timeout_seconds must be between 1 and 600")

    max_steps = policy.get("max_steps")
    if max_steps is not None and (
        isinstance(max_steps, bool) or not isinstance(max_steps, int) or not (1 <= max_steps <= 100)
    ):
        raise SandboxPolicyError("max_steps must be between 1 and 100")

    return {
        "image": effective_image,
        "pids_limit": pids_limit,
        "memory": memory,
        "cpus": float(cpus),
    }


def validate_scorer_config(scorer_config: Any) -> str:
    """Validate scorer_config against the known scorer allowlist (fail-closed)."""
    if scorer_config is None:
        return "unit_test"
    if not isinstance(scorer_config, dict):
        raise SandboxPolicyError("scorer_config must be a JSON object")
    if "name" not in scorer_config:
        return "unit_test"
    scorer_name = scorer_config["name"]
    if not isinstance(scorer_name, str) or scorer_name not in VALID_SCORERS:
        raise SandboxPolicyError(
            f"Unsupported scorer '{scorer_name}'. Must be one of: {sorted(VALID_SCORERS)}"
        )
    return scorer_name


def build_docker_run_args(
    image: str | None,
    workdir_host: str,
    sandbox_policy: dict[str, Any] | None = None,
    *,
    container_name: str | None = None,
    run_id: str = "run",
    sample_id: str = "sample",
    allowed_images: list[str] | None = None,
) -> list[str]:
    """Single source of truth for hardened `docker run` arguments (ADR-010, T7).

    Enforces:
    - `--rm --name matrix-sbx-<run>-<sample>-<rand> --init`
    - `--network none`
    - `--read-only`
    - `--user 65534:65534`
    - `--cap-drop ALL`
    - `--security-opt no-new-privileges:true`
    - `--pids-limit <cfg,128>`
    - `--memory <cfg,512m> --memory-swap <same>`
    - `--cpus <cfg,1,max 4>`
    - `--tmpfs /tmp:rw,noexec,nosuid,size=256m`
    - `--ipc none`
    - `--ulimit nofile=256:256 --ulimit fsize=67108864:67108864`
    - operator image allowlist only; no user mounts/volumes (only runner-owned `/workspace`).
    """
    policy = sandbox_policy or {}
    resolved = validate_sandbox_policy(policy, image=image, allowed_images=allowed_images)

    cname = container_name or (
        f"matrix-sbx-{_safe_name(run_id)}-{_safe_name(sample_id)}-{secrets.token_hex(4)}"
    )
    cpus = resolved["cpus"]
    cpus_str = str(int(cpus)) if cpus == int(cpus) else f"{cpus:g}"
    memory = resolved["memory"]

    return [
        "docker",
        "run",
        "--rm",
        "--name",
        cname,
        "--init",
        "--network",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(resolved["pids_limit"]),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        cpus_str,
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=256m",
        "--ipc",
        "none",
        "--ulimit",
        "nofile=256:256",
        "--ulimit",
        "fsize=67108864:67108864",
        "-v",
        f"{workdir_host}:/workspace:rw",
        "-w",
        "/workspace",
        resolved["image"],
    ]


def is_docker_available() -> bool:
    """Check if the Docker daemon is reachable using a minimal environment."""
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            env=_docker_cli_env(),
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _force_cleanup_container(container_name: str) -> None:
    """Kill and force-remove a container by name, ignoring errors."""
    env = _docker_cli_env()
    for subcmd in (["docker", "kill", container_name], ["docker", "rm", "-f", container_name]):
        try:
            subprocess.run(
                subcmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Ignoring error during container cleanup (%s): %s", subcmd, exc)


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


# --- SandboxEngine Interface & Implementations -----------------------------


class SandboxEngine(abc.ABC):
    """Abstract interface for sandbox execution backends (`docker`, `fake`).

    Per ADR-010, Inspect AI in a dedicated `matrix-runner` service is the target real engine;
    until that service lands, `DockerSandbox` provides hardened per-sample containers and
    `FakeSandbox` provides deterministic test/dev simulation without host execution.
    """

    backend: str
    simulated: bool

    def __init__(self, workdir: Path, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.workdir = workdir.resolve()
        self.timeout_seconds = timeout_seconds

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
        if not self.workdir.exists():
            return files
        for p in self.workdir.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(self.workdir)))
        return sorted(files)

    @abc.abstractmethod
    def execute_bash(self, cmd: str, timeout: float | None = None) -> tuple[int, str, str]:
        """Run a shell command inside the sandbox."""

    @abc.abstractmethod
    def execute_python(self, code: str, timeout: float | None = None) -> tuple[int, str, str]:
        """Run Python code inside the sandbox."""

    def cleanup(self) -> None:
        """Release any engine-owned resources."""


class FakeSandbox(SandboxEngine):
    """Deterministic simulated sandbox for `MATRIX_ENV in {'test', 'dev'}`.

    NEVER executes user code on the host (no subprocess, no eval/exec).
    """

    backend = "fake"
    simulated = True

    def __init__(
        self,
        workdir: Path,
        sandbox_policy: dict[str, Any] | None = None,
    ) -> None:
        if not is_fake_backend_allowed():
            raise SandboxPolicyError(
                "runner_backend='fake' is only allowed when MATRIX_ENV is 'test' or 'dev'"
            )
        policy = sandbox_policy or {}
        timeout = float(policy.get("task_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        super().__init__(workdir=workdir, timeout_seconds=timeout)
        self.sandbox_policy = policy

    def execute_bash(self, cmd: str, timeout: float | None = None) -> tuple[int, str, str]:
        digest = hashlib.sha256(f"bash:{cmd}".encode()).hexdigest()[:12]
        return 0, _truncate_output(f"[simulated:bash] {digest}"), ""

    def execute_python(self, code: str, timeout: float | None = None) -> tuple[int, str, str]:
        digest = hashlib.sha256(f"python:{code}".encode()).hexdigest()[:12]
        return 0, _truncate_output(f"[simulated:python] {digest}"), ""


class DockerSandbox(SandboxEngine):
    """Hardened Docker container execution environment (fail-closed, zero host fallback)."""

    backend = "docker"
    simulated = False

    def __init__(
        self,
        workdir: Path,
        image: str | None = None,
        sandbox_policy: dict[str, Any] | None = None,
        *,
        run_id: str = "run",
        sample_id: str = "sample",
        allowed_images: list[str] | None = None,
    ) -> None:
        policy = sandbox_policy or {}
        resolved = validate_sandbox_policy(policy, image=image, allowed_images=allowed_images)
        timeout = float(policy.get("task_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        super().__init__(workdir=workdir, timeout_seconds=timeout)
        self.image: str = resolved["image"]
        self.sandbox_policy = policy
        self.run_id = run_id
        self.sample_id = sample_id
        self.allowed_images = allowed_images
        self._active_containers: set[str] = set()

        if not is_docker_available():
            raise SandboxUnavailableError("Docker daemon is not available")

    def _run_in_container(self, cmd_argv: list[str], timeout: float | None = None) -> tuple[int, str, str]:
        cname = f"matrix-sbx-{_safe_name(self.run_id)}-{_safe_name(self.sample_id)}-{secrets.token_hex(4)}"
        docker_args = build_docker_run_args(
            self.image,
            str(self.workdir),
            sandbox_policy=self.sandbox_policy,
            container_name=cname,
            run_id=self.run_id,
            sample_id=self.sample_id,
            allowed_images=self.allowed_images,
        )
        full_cmd = docker_args + cmd_argv
        to = timeout or self.timeout_seconds
        self._active_containers.add(cname)
        needs_cleanup = False
        try:
            proc = subprocess.run(
                full_cmd,
                check=False,
                capture_output=True,
                timeout=to,
                env=_docker_cli_env(),
            )
            stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))
            if proc.returncode == 125 and any(m in stderr.lower() for m in _DAEMON_ERROR_MARKERS):
                raise SandboxUnavailableError(f"Docker daemon unavailable: {stderr[:300]}")
            if proc.returncode == 137 and not stderr:
                stderr = "Container killed by OOM (exit code 137)"
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired as exc:
            needs_cleanup = True
            raw_out = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode("utf-8", "replace")
            stdout = _truncate_output(raw_out.decode("utf-8", errors="replace"))
            stderr = _truncate_output(f"Docker execution timed out after {to}s")
            return -1, stdout, stderr
        except (FileNotFoundError, OSError) as exc:
            needs_cleanup = True
            raise SandboxUnavailableError(f"Docker CLI/daemon unavailable: {exc}") from exc
        finally:
            if needs_cleanup:
                _force_cleanup_container(cname)
            self._active_containers.discard(cname)

    def execute_bash(self, cmd: str, timeout: float | None = None) -> tuple[int, str, str]:
        return self._run_in_container(["bash", "-c", cmd], timeout=timeout)

    def execute_python(self, code: str, timeout: float | None = None) -> tuple[int, str, str]:
        return self._run_in_container(["python", "-c", code], timeout=timeout)

    def cleanup(self) -> None:
        for cname in list(self._active_containers):
            _force_cleanup_container(cname)
            self._active_containers.discard(cname)


def create_sandbox_engine(
    backend: str,
    workdir: Path,
    sandbox_policy: dict[str, Any] | None = None,
    *,
    run_id: str = "run",
    sample_id: str = "sample",
) -> SandboxEngine:
    """Instantiate the configured `SandboxEngine` (`docker` or `fake`)."""
    if backend == "docker":
        return DockerSandbox(
            workdir=workdir,
            sandbox_policy=sandbox_policy,
            run_id=run_id,
            sample_id=sample_id,
        )
    if backend == "fake":
        return FakeSandbox(workdir=workdir, sandbox_policy=sandbox_policy)
    raise SandboxPolicyError(f"Unsupported sandbox runner_backend: '{backend}'")


# --- Step Execution Helper -------------------------------------------------


def execute_step(
    sandbox: SandboxEngine,
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
    except (SandboxUnavailableError, SandboxPolicyError):
        raise
    except Exception as exc:  # noqa: BLE001
        stderr = f"Step execution exception: {exc}"
        exit_code = 1

    duration_ms = (time.perf_counter() - start_t) * 1000.0

    in_text = f"{thought or ''} {tool_name} {json.dumps(tool_args)}"
    out_text = f"{stdout} {stderr}"
    input_tokens = tool_args.get("input_tokens") or max(1, len(in_text) // 4)
    output_tokens = tool_args.get("output_tokens") or max(1, len(out_text) // 4)

    record: dict[str, Any] = {
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
    if exit_code == 137:
        record["oom"] = True
    return record


# --- Solvers ---------------------------------------------------------------


def run_solver(
    sandbox: SandboxEngine,
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

    if solver_name == "react":
        s1 = execute_step(
            sandbox=sandbox,
            step_idx=1,
            tool_name="bash",
            tool_args={"cmd": "ls -la"},
            timeout=task_timeout,
            thought=f"Observe initial workspace for task: {task.task_key}",
        )
        executed_steps.append(s1)

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
    sandbox: SandboxEngine,
    task: SandboxTask,
    scorer_config: dict[str, Any],
    steps: list[dict[str, Any]],
    save_blob: Callable[[str, bytes], str | None] | None = None,
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

    if any(s.get("exit_code") == 137 for s in steps):
        status = "oom"
        score = 0.0
        error = "Container killed by OOM (exit code 137)"
        scorer_details["oom"] = True
    elif scorer_name == "unit_test":
        test_cmd = (
            scorer_config.get("command")
            or scorer_config.get("test_cmd")
            or task.test_cmd
        )
        if not test_cmd:
            if task.test_script:
                sandbox.write_file("_test_suite.py", task.test_script)
                test_cmd = "python _test_suite.py"
            elif scorer_config.get("test_script"):
                sandbox.write_file("_test_suite.py", scorer_config["test_script"])
                test_cmd = "python _test_suite.py"
            else:
                test_cmd = "python -c 'import sys; sys.exit(0)'"

        exit_code, stdout, stderr = sandbox.execute_bash(test_cmd, timeout=task_timeout)
        if exit_code == 137:
            status = "oom"
            score = 0.0
            error = "Container killed by OOM (exit code 137)"
            scorer_details.update({
                "command": test_cmd,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "oom": True,
            })
        else:
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
        expected = scorer_config.get("expected_output")
        if expected is None:
            expected = task.scorer_target
        last_out = steps[-1].get("stdout", "") if steps else ""
        if expected is None or not str(expected).strip():
            passed = False
            score = 0.0
            error = "llm_judge requires expected_output (no real judge evaluation configured)"
        else:
            passed = str(expected).strip() in last_out.strip()
            score = 1.0 if passed else 0.0
            if not passed:
                error = "LLM judge evaluation below passing threshold"
        status = "passed" if passed else "failed"
        scorer_details.update({"rubric": rubric, "score": score, "passed": passed})

    else:
        status = "failed"
        score = 0.0
        error = f"Unsupported scorer: {scorer_name}"
        scorer_details.update({"unknown_scorer": scorer_name, "error": error})

    # 2. Artifact verification with SHA-256 via ArtifactStore
    verified_artifacts: list[dict[str, Any]] = []
    artifact_errors: list[str] = []
    artifacts_to_check = set(task.artifacts)
    if "artifacts" in scorer_config:
        artifacts_to_check.update(scorer_config["artifacts"])
    if not artifacts_to_check:
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

            if save_blob is not None:
                try:
                    storage_uri = save_blob(rel_path, data)
                except Exception as store_err:  # noqa: BLE001
                    log.warning("Artifact store write failed for %s: %s", rel_path, store_err)
                    err_msg = f"Artifact store write failed: {store_err}"
                    artifact_errors.append(f"{rel_path}: {err_msg}")
                    verified_artifacts.append({
                        "path": rel_path,
                        "sha256": file_sha,
                        "size_bytes": size_bytes,
                        "verified": False,
                        "storage_uri": None,
                        "error": err_msg,
                    })
                    continue

                if not storage_uri:
                    log.warning("Artifact store returned empty storage URI for %s", rel_path)
                    err_msg = "Artifact store returned empty storage URI"
                    artifact_errors.append(f"{rel_path}: {err_msg}")
                    verified_artifacts.append({
                        "path": rel_path,
                        "sha256": file_sha,
                        "size_bytes": size_bytes,
                        "verified": False,
                        "storage_uri": None,
                        "error": err_msg,
                    })
                    continue

            verified_artifacts.append({
                "path": rel_path,
                "sha256": file_sha,
                "size_bytes": size_bytes,
                "verified": True,
                "storage_uri": storage_uri,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("Artifact verification failed for %s: %s", rel_path, exc)
            err_msg = f"Artifact verification failed: {exc}"
            artifact_errors.append(f"{rel_path}: {err_msg}")
            verified_artifacts.append({
                "path": rel_path,
                "sha256": None,
                "size_bytes": 0,
                "verified": False,
                "storage_uri": None,
                "error": err_msg,
            })

    if artifact_errors:
        scorer_details["artifact_verification_failed"] = True
        scorer_details["artifact_errors"] = artifact_errors
        if status == "passed":
            status = "failed"
            score = 0.0
        combined_err = "; ".join(artifact_errors)
        error = f"{error}; {combined_err}" if error else combined_err

    return status, score, scorer_details, verified_artifacts, error


# --- Orchestration: run_sandbox_job ----------------------------------------


def _blob_saver(
    session_factory: sessionmaker[Session],
    store: ArtifactStore | None,
    project_id: str,
    sandbox_run_id: str,
    store_error: str | None = None,
) -> Callable[[str, bytes], str | None] | None:
    """Store a task's output file through the G0 artifact store (an `artifacts` row owned by the
    sandbox run: deduplicated, within the project quota, removed with the project)."""
    if store_error is not None:
        def fail_save(_rel_path: str, _data: bytes) -> str | None:
            raise RuntimeError(store_error)

        return fail_save
    if store is None:
        return None
    limits = Limits.from_settings(get_settings())

    def save(rel_path: str, data: bytes) -> str | None:
        with session_factory() as session, session.begin():
            art = store_bytes(
                session, store, project_id, rel_path, data,
                owner_kind="sandbox_run", owner_id=sandbox_run_id, limits=limits,
            )
            return f"artifact:{art.id}"

    return save


def run_sandbox_job(
    session_factory: sessionmaker[Session],
    sandbox_run_id: str,
    artifact_store: ArtifactStore | None = None,
) -> None:
    """Execute a complete SandboxRun job against the database."""
    artifact_store_error: str | None = None
    if artifact_store is None:
        try:
            from matrix_server.workers.artifact_handlers import get_artifact_store

            artifact_store = get_artifact_store()
        except Exception as exc:  # noqa: BLE001
            log.error("Could not build default artifact store for sandbox run %s: %s", sandbox_run_id, exc)
            artifact_store_error = f"Artifact store unavailable: {exc}"

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
        run.simulated = run.runner_backend == "fake"
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
    fatal_status: str | None = None
    fatal_error: str | None = None

    try:
        for task in tasks:
            with session_factory() as session:
                current_status = session.scalar(
                    select(SandboxRun.status).where(SandboxRun.id == sandbox_run_id)
                )
                if current_status == "cancelled":
                    log.info("Sandbox run %s cancelled mid-flight", sandbox_run_id)
                    break

            t_start = time.perf_counter()
            tmpdir = tempfile.mkdtemp(prefix="matrix_sandbox_")
            os.chmod(tmpdir, 0o700)
            sandbox: SandboxEngine | None = None
            try:
                workdir = Path(tmpdir)
                sandbox = create_sandbox_engine(
                    runner_backend,
                    workdir=workdir,
                    sandbox_policy=sandbox_policy,
                    run_id=sandbox_run_id,
                    sample_id=task.task_key,
                )

                for fpath, fcontent in task.initial_files.items():
                    sandbox.write_file(fpath, fcontent)

                steps = run_solver(
                    sandbox=sandbox,
                    task=task,
                    solver_config=solver_config,
                    max_steps=max_steps,
                    task_timeout=task_timeout,
                )

                t_status, score, scorer_details, verified_arts, task_err = evaluate_scorer(
                    sandbox=sandbox,
                    task=task,
                    scorer_config=scorer_config,
                    steps=steps,
                    save_blob=_blob_saver(
                        session_factory,
                        artifact_store,
                        project_id,
                        sandbox_run_id,
                        store_error=artifact_store_error,
                    ),
                    project_id=project_id,
                    task_timeout=task_timeout,
                )

                duration_ms = (time.perf_counter() - t_start) * 1000.0
                task_in_tokens = sum(s["input_tokens"] for s in steps)
                task_out_tokens = sum(s["output_tokens"] for s in steps)
                total_tokens_accum += task_in_tokens + task_out_tokens
                total_cost_usd_accum += (task_in_tokens + task_out_tokens) * COST_PER_TOKEN_USD

                transcript = SandboxTaskTranscript(
                    project_id=project_id,
                    sandbox_run_id=sandbox_run_id,
                    task_key=task.task_key,
                    status=t_status,
                    simulated=sandbox.simulated,
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
            finally:
                if sandbox is not None:
                    sandbox.cleanup()
                shutil.rmtree(tmpdir, ignore_errors=True)

    except SandboxUnavailableError as exc:
        log.error("Sandbox unavailable for run %s: %s", sandbox_run_id, exc)
        fatal_status = "sandbox_unavailable"
        fatal_error = str(exc)[:2000]
    except Exception as exc:
        log.exception("Fatal error during sandbox run %s", sandbox_run_id)
        fatal_status = "failed"
        fatal_error = str(exc)[:2000]

    # 4. Finalize SandboxRun aggregates
    with session_factory() as session, session.begin():
        run = session.get(SandboxRun, sandbox_run_id)
        if run is not None:
            if run.status != "cancelled":
                if fatal_error:
                    run.status = fatal_status or "failed"
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
