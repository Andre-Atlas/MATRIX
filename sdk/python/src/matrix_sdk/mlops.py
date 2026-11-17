"""MLOps Studio client: TrainingRun, MLOpsClient, and MatrixTrainerCallback."""

from __future__ import annotations

import atexit
import hashlib
import logging
import math
import os
import queue
import re
import tempfile
import threading
import time
import warnings
import weakref
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlparse

import httpx

from .client import MatrixError, _seg

if TYPE_CHECKING:
    from .client import MatrixClient

log = logging.getLogger("matrix_sdk.mlops")

_CHUNK_BYTES = 8 * 1024 * 1024  # 8 MB
_MAX_QUEUE_SIZE = 10_000
_DEFAULT_BATCH_SIZE = 200
_STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SweepExhausted(MatrixError):
    """Raised when a sweep has no more trials to suggest."""


def _validate_finite(obj: Any, *, label: str = "value") -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"{label} must be finite (no NaN or Infinity)")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _validate_finite(k, label=label)
            _validate_finite(v, label=label)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _validate_finite(item, label=label)


def _iter_file_chunks(path: Path, chunk_size: int = _CHUNK_BYTES) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _compute_sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    for chunk in _iter_file_chunks(path):
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def _is_internal_relative_url(url: str) -> bool:
    return url.startswith("/") and not url.startswith("//")


def _validate_external_url(url: str) -> None:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported external URL scheme: {scheme!r}")


def _finish_run_on_exit(run_ref: weakref.ref[TrainingRun]) -> None:
    run = run_ref()
    if run is not None and not run._finished:
        try:
            run.finish(status="completed")
        except Exception as exc:  # noqa: BLE001
            log.debug("Failed to finish run %s on exit: %s", run.id, exc)


class TrainingRun:
    """Context-managed training run with async buffered metric logging and artifact tracking."""

    def __init__(
        self,
        client: MatrixClient,
        run_data: dict[str, Any],
        *,
        flush_interval: float = 2.0,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._client = client
        self.data: dict[str, Any] = dict(run_data)
        self.id: str = str(run_data["id"])
        self.name: str = str(run_data.get("name", ""))
        self.status: str = str(run_data.get("status", "running"))
        self.should_stop: bool = False
        self.stop_reason: str | None = None

        self._flush_interval = max(0.05, float(flush_interval))
        self._batch_size = max(1, min(1000, int(batch_size)))

        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._drained_cond = threading.Condition()
        self._sending = False
        self._finished = False
        self._finish_lock = threading.Lock()

        self._sys_stop_event = threading.Event()
        self._sys_lock = threading.Lock()
        self._sys_thread: threading.Thread | None = None
        self._sys_warned = False

        self._worker = threading.Thread(
            target=self._drain_loop,
            daemon=True,
            name=f"matrix-mlops-run-{self.id}",
        )
        self._worker.start()

        run_ref = weakref.ref(self)
        self._atexit_cb: Callable[[], None] = lambda ref=run_ref: _finish_run_on_exit(ref)
        atexit.register(self._atexit_cb)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            err_msg = f"{exc_type.__name__}: {exc_val}"
            self.finish(status="failed", error=err_msg[:10_000])
        elif not self._finished:
            self.finish(status="completed")
        return False

    def log(
        self,
        metrics: dict[str, float],
        step: int,
        epoch: float | None = None,
        category: str = "train",
    ) -> None:
        """Enqueue a dictionary of scalar metrics for asynchronous batched upload."""
        if self._finished or self._stop_event.is_set():
            log.warning("Ignoring log() call on finished run %s", self.id)
            return
        step_int = int(step)
        if step_int < 0:
            raise ValueError("step must be >= 0")
        if epoch is not None:
            if isinstance(epoch, bool) or not math.isfinite(float(epoch)):
                raise ValueError("epoch must be finite (no NaN or Infinity)")
            epoch_val: float | None = float(epoch)
        else:
            epoch_val = None

        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        points: list[dict[str, Any]] = []
        for metric_name, raw_val in metrics.items():
            if isinstance(raw_val, bool):
                raise TypeError(f"metric '{metric_name}' must be a finite number, not bool")
            val = float(raw_val)
            if not math.isfinite(val):
                raise ValueError(f"metric '{metric_name}' must be finite (no NaN or Infinity)")
            point: dict[str, Any] = {
                "category": category,
                "metric_name": str(metric_name),
                "step": step_int,
                "value": val,
                "recorded_at": now_iso,
            }
            if epoch_val is not None:
                point["epoch"] = epoch_val
            points.append(point)

        for pt in points:
            try:
                self._queue.put(pt, timeout=1.0)
            except queue.Full:
                log.warning("Metric queue full for run %s; dropping metric %s", self.id, pt["metric_name"])

        if self._queue.qsize() >= self._batch_size:
            self._wake_event.set()

    def _drain_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            self._wake_event.wait(timeout=self._flush_interval)
            self._wake_event.clear()

            while True:
                batch: list[dict[str, Any]] = []
                with self._drained_cond:
                    while len(batch) < self._batch_size:
                        try:
                            batch.append(self._queue.get_nowait())
                        except queue.Empty:
                            break
                    if batch:
                        self._sending = True
                    else:
                        self._drained_cond.notify_all()
                        break

                try:
                    self._send_batch(batch)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to send metrics batch for run %s: %s", self.id, exc)
                finally:
                    for _ in batch:
                        self._queue.task_done()
                    with self._drained_cond:
                        self._sending = False
                        if self._queue.empty():
                            self._drained_cond.notify_all()

    def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        resp = self._client._request(
            "POST",
            f"/api/v1/mlops/runs/{_seg(self.id)}/metrics",
            json={"points": batch},
        )
        if isinstance(resp, dict) and resp.get("should_stop"):
            if resp.get("stop_reason") is not None:
                self.stop_reason = str(resp["stop_reason"])
            self.should_stop = True

    def flush(self, timeout: float = 10.0) -> None:
        """Synchronously wait until all queued metrics have been sent or timeout expires."""
        deadline = time.monotonic() + timeout
        self._wake_event.set()
        with self._drained_cond:
            while (not self._queue.empty() or self._sending) and self._worker.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._wake_event.set()
                self._drained_cond.wait(timeout=min(remaining, 0.2))

    def finish(self, status: str = "completed", error: str | None = None) -> dict[str, Any]:
        """Flush pending metrics, stop background threads, and mark the run finished."""
        with self._finish_lock:
            if self._finished:
                return self.data

            self._sys_stop_event.set()
            if self._sys_thread is not None and self._sys_thread.is_alive():
                self._sys_thread.join(timeout=1.0)

            self.flush(timeout=10.0)
            self._stop_event.set()
            self._wake_event.set()
            if self._worker.is_alive():
                self._worker.join(timeout=2.0)

            payload: dict[str, Any] = {"status": status}
            if error is not None:
                payload["error"] = error[:10_000]

            try:
                resp = self._client._request(
                    "PATCH",
                    f"/api/v1/mlops/runs/{_seg(self.id)}",
                    json=payload,
                )
                if isinstance(resp, dict):
                    self.data = resp
                    self.status = str(resp.get("status", status))
                else:
                    self.status = status
            finally:
                self._finished = True
                atexit.unregister(self._atexit_cb)

            return self.data

    def close(self) -> None:
        """Flush pending metrics and finish the run if not already finished."""
        self.finish(status="completed")

    def log_artifact(
        self,
        path: str | Path,
        name: str,
        kind: str = "lora_adapter",
        format: str = "safetensors",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hash, presign, stream-upload (unless deduplicated), and commit a file artifact."""
        p = Path(path)
        if p.is_symlink():
            raise ValueError(f"artifact path must not be a symlink: {path}")
        if not p.exists():
            raise FileNotFoundError(f"artifact path does not exist: {path}")
        if not p.is_file():
            raise ValueError(f"artifact path must be a regular file: {path}")

        sha256_hex, size_bytes = _compute_sha256_and_size(p)
        meta = dict(metadata or {})
        meta.setdefault("format", format)
        _validate_finite(meta, label="metadata")

        content_type = "application/octet-stream"
        presign_resp = self._client._request(
            "POST",
            "/api/v1/mlops/artifacts/presign-upload",
            json={
                "name": name,
                "kind": kind,
                "sha256": sha256_hex,
                "size_bytes": size_bytes,
                "content_type": content_type,
                "run_id": self.id,
                "metadata": meta,
            },
        )

        if presign_resp.get("deduplicated"):
            return dict(presign_resp)

        upload_url = presign_resp.get("upload_url")
        if upload_url:
            upload_url_str = str(upload_url)
            headers = {
                "Content-Type": content_type,
                "Content-Length": str(size_bytes),
            }
            if _is_internal_relative_url(upload_url_str):
                put_resp = self._client._http.put(
                    upload_url_str,
                    content=_iter_file_chunks(p),
                    headers=headers,
                    timeout=_STREAM_TIMEOUT,
                )
                put_resp.raise_for_status()
            else:
                _validate_external_url(upload_url_str)
                # Never forward MATRIX Authorization header to external S3/MinIO presigned URLs.
                with httpx.Client(timeout=_STREAM_TIMEOUT) as s3_http:
                    put_resp = s3_http.put(
                        upload_url_str,
                        content=_iter_file_chunks(p),
                        headers=headers,
                    )
                    put_resp.raise_for_status()

        committed = self._client._request(
            "POST",
            f"/api/v1/mlops/artifacts/{_seg(presign_resp['id'])}/commit",
            json={"source_run_id": self.id, "size_bytes": size_bytes},
        )
        return dict(committed)

    def log_classic_eval(self, classic_eval: dict[str, Any]) -> dict[str, Any]:
        """Attach classic ML evaluation payloads (ROC-AUC, confusion matrix, SHAP) to the run."""
        _validate_finite(classic_eval, label="classic_eval")
        resp = self._client._request(
            "PATCH",
            f"/api/v1/mlops/runs/{_seg(self.id)}",
            json={"classic_eval": classic_eval},
        )
        if isinstance(resp, dict):
            self.data = resp
        return dict(resp)

    def start_system_monitor(self, interval_s: float = 10.0) -> None:
        """Start optional background CPU/RAM/GPU telemetry if psutil or pynvml is installed."""
        with self._sys_lock:
            if self._sys_thread is not None and self._sys_thread.is_alive():
                return

            try:
                import psutil  # type: ignore[import-not-found]
            except ImportError:
                psutil = None

            try:
                import pynvml  # type: ignore[import-not-found]

                pynvml.nvmlInit()
            except Exception:  # noqa: BLE001
                pynvml = None

            if psutil is None and pynvml is None:
                if not self._sys_warned:
                    self._sys_warned = True
                    warnings.warn(
                        "psutil and pynvml are not installed; system telemetry monitoring disabled.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                return

            interval = max(0.5, float(interval_s))

            def _sys_loop() -> None:
                try:
                    step = 0
                    while not self._sys_stop_event.wait(timeout=interval):
                        metrics: dict[str, float] = {}
                        if psutil is not None:
                            metrics["cpu_percent"] = float(psutil.cpu_percent(interval=None))
                            vm = psutil.virtual_memory()
                            metrics["memory_percent"] = float(vm.percent)
                            metrics["memory_used_mb"] = float(vm.used / (1024 * 1024))
                        if pynvml is not None:
                            try:
                                count = pynvml.nvmlDeviceGetCount()
                                if count > 0:
                                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                                    metrics["gpu_utilization"] = float(util.gpu)
                                    metrics["gpu_memory_used_mb"] = float(mem.used / (1024 * 1024))
                            except Exception as exc:  # noqa: BLE001
                                log.debug("GPU telemetry read failed: %s", exc)
                        if metrics:
                            try:
                                self.log(metrics, step=step, category="system")
                            except Exception as exc:  # noqa: BLE001
                                log.debug("System telemetry log failed: %s", exc)
                        step += 1
                finally:
                    if pynvml is not None:
                        try:
                            pynvml.nvmlShutdown()
                        except Exception as exc:  # noqa: BLE001
                            log.debug("nvmlShutdown failed: %s", exc)

            self._sys_thread = threading.Thread(
                target=_sys_loop,
                daemon=True,
                name=f"matrix-mlops-sysmon-{self.id}",
            )
            self._sys_thread.start()


class MLOpsClient:
    """High-level MLOps Studio client for runs, sweeps, artifacts, and model registry."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    def start_run(
        self,
        name: str,
        *,
        run_type: str = "lora",
        framework: str = "pytorch",
        base_model: str | None = None,
        dataset_id: str | None = None,
        sweep_id: str | None = None,
        parent_run_id: str | None = None,
        hyperparameters: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        hardware_info: dict[str, Any] | None = None,
        git_commit: str | None = None,
        flush_interval: float = 2.0,
    ) -> TrainingRun:
        if hyperparameters is not None:
            _validate_finite(hyperparameters, label="hyperparameters")
        if hardware_info is not None:
            _validate_finite(hardware_info, label="hardware_info")

        body: dict[str, Any] = {
            "name": name,
            "run_type": run_type,
            "status": "running",
            "framework": framework,
            "hyperparams": dict(hyperparameters or {}),
            "tags": list(tags or []),
        }
        if base_model is not None:
            body["base_model"] = base_model
        if dataset_id is not None:
            body["dataset_id"] = dataset_id
        if sweep_id is not None:
            body["sweep_id"] = sweep_id
        if parent_run_id is not None:
            body["parent_run_id"] = parent_run_id
        if hardware_info is not None:
            body["hardware_info"] = hardware_info
        if git_commit is not None:
            body["git_commit"] = git_commit

        run_data = self._client._request("POST", "/api/v1/mlops/runs", json=body)
        return TrainingRun(self._client, run_data, flush_interval=flush_interval)

    def create_sweep(
        self,
        name: str,
        *,
        method: str = "random",
        metric_name: str = "eval/loss",
        metric_goal: str = "minimize",
        search_space: dict[str, Any],
        early_stopping: dict[str, Any] | None = None,
        max_trials: int = 20,
        max_concurrent: int = 4,
        description: str | None = None,
    ) -> dict[str, Any]:
        _validate_finite(search_space, label="search_space")
        body: dict[str, Any] = {
            "name": name,
            "method": method,
            "metric_name": metric_name,
            "metric_goal": metric_goal,
            "search_space": search_space,
            "max_trials": max_trials,
            "max_concurrent": max_concurrent,
        }
        if early_stopping is not None:
            body["early_stopping"] = early_stopping
        if description is not None:
            body["description"] = description
        return dict(self._client._request("POST", "/api/v1/mlops/sweeps", json=body))

    def run_sweep_agent(
        self,
        sweep_id: str,
        train_fn: Callable[[TrainingRun, dict[str, Any]], Any],
        *,
        count: int | None = None,
    ) -> None:
        executed = 0
        while count is None or executed < count:
            try:
                suggestion = self._client._request(
                    "POST",
                    f"/api/v1/mlops/sweeps/{_seg(sweep_id)}/trials/next",
                    json={},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    break
                raise
            except SweepExhausted:
                break

            run_data = suggestion["run"]
            hyperparams = dict(suggestion.get("hyperparams") or run_data.get("hyperparams") or {})
            with TrainingRun(self._client, run_data) as run:
                train_fn(run, hyperparams)
            executed += 1

    def register_model(
        self,
        name: str,
        *,
        task_type: str = "lora",
        base_model: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "model_type": task_type,
            "tags": list(tags or []),
        }
        if base_model is not None:
            body["base_model"] = base_model
        if description is not None:
            body["description"] = description
        return dict(self._client._request("POST", "/api/v1/mlops/models", json=body))

    def create_model_version(
        self,
        model_id: str,
        *,
        artifact_id: str,
        run_id: str | None = None,
        lora_config: dict[str, Any] | None = None,
        signature: dict[str, Any] | None = None,
        metrics_snapshot: dict[str, Any] | None = None,
        stage: str = "none",
        eval_experiment_id: str | None = None,
        safety_scan_id: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        if metrics_snapshot is not None:
            _validate_finite(metrics_snapshot, label="metrics_snapshot")
        body: dict[str, Any] = {
            "artifact_id": artifact_id,
            "stage": stage,
            "metrics_snapshot": dict(metrics_snapshot or {}),
        }
        if run_id is not None:
            body["run_id"] = run_id
        if lora_config is not None:
            body["lora_config"] = lora_config
        if signature is not None:
            body["signature"] = signature
        if eval_experiment_id is not None:
            body["eval_experiment_id"] = eval_experiment_id
        if safety_scan_id is not None:
            body["safety_scan_id"] = safety_scan_id
        if commit_message is not None:
            body["model_card"] = commit_message

        return dict(
            self._client._request(
                "POST",
                f"/api/v1/mlops/models/{_seg(model_id)}/versions",
                json=body,
            )
        )

    def set_model_alias(self, model_id: str, alias: str, version: int) -> dict[str, Any]:
        return dict(
            self._client._request(
                "POST",
                f"/api/v1/mlops/models/{_seg(model_id)}/aliases",
                json={"alias": alias, "version": int(version)},
            )
        )

    def download_model(
        self,
        model_id: str,
        *,
        alias: str = "production",
        version: int | None = None,
        dest_dir: str | Path = ".",
    ) -> Path:
        model_data = self._client._request("GET", f"/api/v1/mlops/models/{_seg(model_id)}")
        if version is None:
            alias_key = alias.lstrip("@")
            aliases = model_data.get("aliases") or {}
            if alias_key not in aliases:
                raise MatrixError(f"Alias '{alias}' not found for model '{model_id}'")
            version = int(aliases[alias_key])

        versions = model_data.get("versions") or []
        target_mv = next((v for v in versions if v.get("version") == version), None)
        if target_mv is None:
            raise MatrixError(f"Version {version} not found for model '{model_id}'")

        artifact_id = target_mv.get("artifact_id")
        if not artifact_id:
            raise MatrixError(f"Model version {version} has no associated artifact_id")

        dl_info = self._client._request(
            "GET",
            f"/api/v1/mlops/artifacts/{_seg(artifact_id)}/download-url",
        )
        download_url = str(dl_info["download_url"])
        expected_sha256 = str(dl_info.get("sha256") or "").lower()
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise MatrixError(f"Invalid expected SHA-256 digest for artifact {artifact_id}: {expected_sha256!r}")

        if not _is_internal_relative_url(download_url):
            _validate_external_url(download_url)

        dest_root = Path(dest_dir)
        dest_root.mkdir(parents=True, exist_ok=True)
        safe_model_name = Path(str(model_data.get("name") or model_id)).name.replace(" ", "_")
        final_path = dest_root / f"{safe_model_name}-v{version}.bin"

        with tempfile.NamedTemporaryFile(
            dir=dest_root,
            delete=False,
            prefix=f".{safe_model_name}-v{version}-",
            suffix=".part",
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)

        hasher = hashlib.sha256()

        def _stream_into_tmp(http_client: httpx.Client) -> None:
            with http_client.stream("GET", download_url, timeout=_STREAM_TIMEOUT) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=_CHUNK_BYTES):
                        if chunk:
                            hasher.update(chunk)
                            fh.write(chunk)

        try:
            if _is_internal_relative_url(download_url):
                _stream_into_tmp(self._client._http)
            else:
                with httpx.Client(timeout=_STREAM_TIMEOUT) as ext_http:
                    _stream_into_tmp(ext_http)

            actual_sha256 = hasher.hexdigest().lower()
            if actual_sha256 != expected_sha256:
                raise MatrixError(
                    f"SHA-256 mismatch for artifact {artifact_id}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            os.replace(tmp_path, final_path)
            return final_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


class MatrixTrainerCallback:
    """Duck-typed Hugging Face / TRL TrainerCallback that logs metrics and propagates early stopping."""

    def __init__(self, run: TrainingRun) -> None:
        self.run = run

    @staticmethod
    def _extract_numeric(payload: dict[str, Any] | None) -> dict[str, float]:
        if not payload:
            return {}
        out: dict[str, float] = {}
        for k, v in payload.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                fval = float(v)
                if math.isfinite(fval):
                    out[str(k)] = fval
        return out

    def on_log(
        self,
        args: Any,
        state: Any,
        control: Any,
        logs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        numeric = self._extract_numeric(logs)
        if numeric:
            step = int(getattr(state, "global_step", 0) or 0)
            epoch_raw = getattr(state, "epoch", None)
            epoch = float(epoch_raw) if isinstance(epoch_raw, (int, float)) else None

            train_metrics = {k: v for k, v in numeric.items() if not k.startswith("eval_")}
            eval_metrics = {k: v for k, v in numeric.items() if k.startswith("eval_")}
            if train_metrics:
                self.run.log(train_metrics, step=step, epoch=epoch, category="train")
            if eval_metrics:
                self.run.log(eval_metrics, step=step, epoch=epoch, category="eval")

        if self.run.should_stop and control is not None:
            control.should_training_stop = True
        return control

    def on_evaluate(
        self,
        args: Any,
        state: Any,
        control: Any,
        metrics: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        numeric = self._extract_numeric(metrics)
        if numeric:
            step = int(getattr(state, "global_step", 0) or 0)
            epoch_raw = getattr(state, "epoch", None)
            epoch = float(epoch_raw) if isinstance(epoch_raw, (int, float)) else None
            self.run.log(numeric, step=step, epoch=epoch, category="eval")

        if self.run.should_stop and control is not None:
            control.should_training_stop = True
        return control

    def on_step_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        if self.run.should_stop and control is not None:
            control.should_training_stop = True
        return control

    def on_train_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        self.run.flush()
        return control
