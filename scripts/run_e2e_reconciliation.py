#!/usr/bin/env python3
"""End-to-end Playwright reconciliation runner.

Starts `matrix-api` and `next start`, bootstraps an owner user + project + API key,
executes a canonical training run, 2-trial sweep agent, `log_model()` artifact + registry upload,
and a simulated benchmark via the real Python SDK (`matrix_sdk`), then runs Playwright
`apps/web/e2e/reconciliation.spec.ts` with `--trace off`.
"""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from matrix_sdk import MatrixClient

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "apps" / "web"
RUN_SUFFIX = time.strftime("%Y%m%d%H%M%S")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _poll(fn: Any, timeout: float = 90.0, every: float = 0.4) -> Any:
    deadline = time.monotonic() + timeout
    val = fn()
    while not val and time.monotonic() < deadline:
        time.sleep(every)
        val = fn()
    return val


def _start(cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    with log_path.open("wb") as log_file:
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _dump_logs(api_log: Path, web_log: Path) -> None:
    for label, path in (("matrix-api", api_log), ("next-web", web_log)):
        if path.exists():
            tail = path.read_text(encoding="utf-8", errors="replace")[-2000:]
            print(f"\n--- {label} log tail ({path}) ---\n{tail}", file=sys.stderr)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="matrix-e2e-"))
    api_port = _free_port()
    web_port = _free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    web_origin = f"http://127.0.0.1:{web_port}"
    admin_token = secrets.token_urlsafe(24)
    secret_key = secrets.token_urlsafe(32)

    api_env = {
        **os.environ,
        "MATRIX_DATABASE_URL": os.environ.get("E2E_DATABASE_URL", f"sqlite:///{tmp / 'e2e.db'}"),
        "MATRIX_SPAN_STORE": os.environ.get("E2E_SPAN_STORE", "memory"),
        "MATRIX_ARTIFACT_BACKEND": "local",
        "MATRIX_ARTIFACT_LOCAL_DIR": str(tmp / "artifacts"),
        "MATRIX_SECRET_KEY": secret_key,
        "MATRIX_ADMIN_TOKEN": admin_token,
        "MATRIX_JUDGE_PROVIDER": "fake",
        "MATRIX_HOST": "127.0.0.1",
        "MATRIX_PORT": str(api_port),
    }
    web_env = {
        **os.environ,
        "MATRIX_MOCK": "0",
        "MATRIX_API_URL": api_url,
        "MATRIX_WEB_ORIGIN": web_origin,
        "MATRIX_INSECURE_COOKIES": "1",
    }

    procs: list[subprocess.Popen] = []
    api_log = tmp / "api.log"
    web_log = tmp / "web.log"
    keep_tmp = False
    try:
        procs.append(_start(["uv", "run", "--project", "server", "matrix-api"], ROOT, api_env, api_log))
        procs.append(
            _start(["npx", "next", "start", "-H", "127.0.0.1", "-p", str(web_port)], WEB_DIR, web_env, web_log)
        )

        def _up(url: str, proc: subprocess.Popen, log: Path) -> bool:
            if proc.poll() is not None:
                raise RuntimeError(f"process exited early ({proc.returncode}):\n{log.read_text()[-1500:]}")
            try:
                return httpx.get(url, timeout=2, trust_env=False).status_code == 200
            except httpx.HTTPError:
                return False

        if not _poll(lambda: _up(f"{api_url}/health", procs[0], api_log)):
            raise RuntimeError(f"matrix-api did not become ready:\n{api_log.read_text()[-1500:]}")
        if not _poll(lambda: _up(f"{web_origin}/login", procs[1], web_log)):
            raise RuntimeError(f"next server did not become ready:\n{web_log.read_text()[-1500:]}")

        email = f"e2e-{RUN_SUFFIX}-{secrets.token_hex(4)}@matrix.local"
        password = secrets.token_urlsafe(24)
        with httpx.Client(
            base_url=api_url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=30,
            trust_env=False,
        ) as admin:
            admin.post(
                "/api/v1/admin/users",
                json={"email": email, "name": "E2E Owner", "password": password},
            ).raise_for_status()
            created = admin.post(
                "/api/v1/admin/projects",
                json={"name": f"e2e-proj-{RUN_SUFFIX}", "owner_email": email},
            )
            created.raise_for_status()
            api_key = created.json()["api_key"]

        run_name = f"e2e-run-{RUN_SUFFIX}"
        sweep_name = f"e2e-sweep-{RUN_SUFFIX}"
        model_name = f"e2e-model-{RUN_SUFFIX}"
        bench_name = f"e2e-bench-{RUN_SUFFIX}"

        client = MatrixClient(api_key=api_key, endpoint=api_url)
        try:
            with client.start_run(
                name=run_name,
                config={"lr": 2e-4, "lora_r": 16},
                tags=["e2e"],
                system_metrics=False,
                flush_interval=0.1,
            ) as run:
                run_id = run.id
                for step, loss in enumerate((0.92, 0.54, 0.31), start=1):
                    run.log_metric("loss", loss, step=step)

            sweep_id = client.sweep(
                {
                    "method": "random",
                    "metric": {"key": "val_loss", "goal": "minimize"},
                    "parameters": {
                        "lr": {"type": "float", "min": 1e-5, "max": 1e-2, "log": True},
                        "batch_size": {"type": "choice", "values": [16, 32]},
                    },
                    "max_runs": 4,
                },
                sweep_name,
            )

            trial_values = iter([0.28, 0.14])

            def _trial() -> None:
                with client.start_run(system_metrics=False, flush_interval=0.1) as trial_run:
                    trial_run.log_metric("val_loss", next(trial_values), step=1)

            client.agent(sweep_id, _trial, count=2, agent_id="e2e-agent")

            weights_path = tmp / "adapter.safetensors"
            weights_path.write_bytes(b"matrix-e2e-safetensors-weights-v1")
            logged = client.registry.log_model(
                weights_path,
                model_name,
                model_type="lora",
                run_id=run_id,
                metrics_snapshot={"val_loss": 0.14},
            )
            mv_id = str(logged["id"])

            bench_resp = client.benchmarks.run(
                name=bench_name,
                suite="lm_eval",
                tasks=["gsm8k"],
                target_model=model_name,
                runner_backend="local_sim",
                model_version_id=mv_id,
                sync_exec=True,
            )
            if bench_resp.get("simulated") is not True:
                raise RuntimeError("expected simulated=true on local_sim benchmark")
        finally:
            client.shutdown()

        pw_env = {
            **os.environ,
            "E2E_BASE_URL": web_origin,
            "E2E_EMAIL": email,
            "E2E_PASSWORD": password,
            "E2E_RUN_NAME": run_name,
            "E2E_SWEEP_NAME": sweep_name,
            "E2E_MODEL_NAME": model_name,
            "E2E_MV_ID": mv_id,
            "E2E_BENCH_NAME": bench_name,
        }
        res = subprocess.run(
            ["npx", "playwright", "test", "e2e/reconciliation.spec.ts", "--trace", "off"],
            check=False,
            cwd=WEB_DIR,
            env=pw_env,
            timeout=120,
        )
        if res.returncode != 0:
            keep_tmp = True
            _dump_logs(api_log, web_log)
        return res.returncode
    except Exception:
        keep_tmp = True
        _dump_logs(api_log, web_log)
        raise
    finally:
        for p in procs:
            _stop(p)
        if not keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
