"""Deprecated MLOps Studio compatibility layer over canonical tracking, sweeps, and registry.

All methods on `MLOpsClient` emit `DeprecationWarning` and delegate to `matrix_sdk.tracking`,
`matrix_sdk.sweeps`, and `matrix_sdk.registry` with no second HTTP stack and no calls to
removed/deprecated `/api/v1/mlops/runs*`, `/api/v1/mlops/artifacts*`, or `/api/v1/mlops/sweeps*`.
"""

from __future__ import annotations

import logging
import math
import threading
import warnings
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from .client import MatrixError
from .registry import _validate_finite
from .sweeps import agent as canonical_agent
from .sweeps import sweep as canonical_sweep
from .tracking import Run
from .tracking import start_run as canonical_start_run

if TYPE_CHECKING:
    from .client import MatrixClient

log = logging.getLogger("matrix_sdk.mlops")

_DEFAULT_BATCH_SIZE = 200
_STATUS_TO_CANONICAL = {
    "completed": "finished",
    "finished": "finished",
    "failed": "failed",
    "cancelled": "killed",
    "stopped": "killed",
    "killed": "killed",
}
_ARTIFACT_KIND_MAP = {
    "lora_adapter": "model",
    "checkpoint": "model",
    "weights": "model",
    "model": "model",
    "plot": "plot",
    "table": "table",
    "report": "report",
    "file": "file",
}


class SweepExhausted(MatrixError):
    """Raised when a sweep has no more trials to suggest."""


def _translate_search_space(search_space: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy main-branch search_space entries to canonical G2 parameter specs."""
    params: dict[str, Any] = {}
    for key, spec in search_space.items():
        if isinstance(spec, dict):
            entry = dict(spec)
            raw_type = entry.get("type") or entry.get("distribution")
            if raw_type in ("categorical", "choice") or (
                raw_type is None and ("values" in entry or "choices" in entry)
            ):
                raw_vals = entry.get("values") if "values" in entry else entry.get("choices")
                params[key] = {"type": "choice", "values": list(raw_vals or [])}
            elif raw_type in ("int_uniform", "q_uniform", "int"):
                out: dict[str, Any] = {"type": "int", "min": entry["min"], "max": entry["max"]}
                if "step" in entry:
                    out["step"] = entry["step"]
                if "log" in entry:
                    out["log"] = bool(entry["log"])
                params[key] = out
            elif raw_type in ("log_uniform", "log_uniform_values"):
                out = {"type": "float", "min": entry["min"], "max": entry["max"], "log": True}
                if "q" in entry:
                    out["q"] = entry["q"]
                params[key] = out
            elif raw_type in ("uniform", "float"):
                out = {"type": "float", "min": entry["min"], "max": entry["max"]}
                if "log" in entry:
                    out["log"] = bool(entry["log"])
                if "q" in entry:
                    out["q"] = entry["q"]
                if "values" in entry:
                    out["values"] = list(entry["values"])
                params[key] = out
            elif raw_type == "constant":
                params[key] = {"type": "constant", "value": entry["value"]} if "value" in entry else entry
            elif "min" in entry and "max" in entry:
                out = {"type": "float", "min": entry["min"], "max": entry["max"]}
                if "log" in entry:
                    out["log"] = bool(entry["log"])
                if "q" in entry:
                    out["q"] = entry["q"]
                params[key] = out
            else:
                params[key] = entry
        elif isinstance(spec, (list, tuple)):
            params[key] = {"type": "choice", "values": list(spec)}
        else:
            params[key] = {"type": "choice", "values": [spec]}
    return params


def _translate_early_stopping(early_stopping: dict[str, Any] | None) -> dict[str, Any] | None:
    if not early_stopping:
        return None
    raw_type = str(early_stopping.get("type") or early_stopping.get("method") or "asha").lower()
    if raw_type not in ("asha", "hyperband"):
        raw_type = "asha"
    out: dict[str, Any] = {"type": raw_type}
    if "min_iter" in early_stopping:
        out["min_iter"] = int(early_stopping["min_iter"])
    elif "min_t" in early_stopping:
        out["min_iter"] = int(early_stopping["min_t"])
    if "max_iter" in early_stopping:
        out["max_iter"] = int(early_stopping["max_iter"])
    elif "max_t" in early_stopping:
        out["max_iter"] = int(early_stopping["max_t"])
    if "eta" in early_stopping:
        out["eta"] = int(early_stopping["eta"])
    elif "reduction_factor" in early_stopping:
        out["eta"] = int(early_stopping["reduction_factor"])
    return out


class TrainingRun:
    """Deprecated compatibility wrapper around canonical `matrix_sdk.tracking.Run`."""

    def __init__(
        self,
        client: MatrixClient,
        run_data: dict[str, Any],
        *,
        flush_interval: float = 2.0,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        _run: Run | None = None,
    ) -> None:
        self._client = client
        self._run: Run = (
            _run
            if _run is not None
            else Run(
                client,
                dict(run_data),
                flush_interval=max(0.05, float(flush_interval)),
                batch_size=max(1, min(1000, int(batch_size))),
            )
        )
        self.data: dict[str, Any] = dict(run_data)
        self.id: str = self._run.id
        self.name: str = self._run.name
        self.status: str = str(run_data.get("status", "running"))
        self.should_stop: bool = False
        self.stop_reason: str | None = None
        self._finished = False
        self._finish_lock = threading.Lock()

    def _sync_pruning(self) -> None:
        if self._run._sweep_id and self._run.should_stop():
            self.should_stop = True
            if self.stop_reason is None:
                self.stop_reason = "pruned"

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
        """Enqueue a dictionary of scalar metrics via canonical `Run.log()`."""
        if self._finished:
            log.warning("Ignoring log() call on finished run %s", self.id)
            return
        step_int = int(step)
        if step_int < 0:
            raise ValueError("step must be >= 0")
        epoch_val: float | None = None
        if epoch is not None:
            if isinstance(epoch, bool) or not math.isfinite(float(epoch)):
                raise ValueError("epoch must be finite (no NaN or Infinity)")
            epoch_val = float(epoch)

        clean_metrics: dict[str, float] = {}
        for metric_name, raw_val in metrics.items():
            if isinstance(raw_val, bool):
                raise TypeError(f"metric '{metric_name}' must be a finite number, not bool")
            val = float(raw_val)
            if not math.isfinite(val):
                raise ValueError(f"metric '{metric_name}' must be finite (no NaN or Infinity)")
            clean_metrics[str(metric_name)] = val

        if category == "system":
            sys_metrics = {
                (k if k.startswith("sys/") else f"sys/{k}"): v for k, v in clean_metrics.items()
            }
            self._run._log_system(sys_metrics, step=step_int)
        else:
            if epoch_val is not None:
                clean_metrics["epoch"] = epoch_val
            self._run.log(clean_metrics, step=step_int)
            self._sync_pruning()

    def flush(self, timeout: float = 10.0) -> None:
        """Synchronously wait until all queued metrics have been sent."""
        self._run.flush(timeout=timeout)
        self._sync_pruning()

    def finish(self, status: str = "completed", error: str | None = None) -> dict[str, Any]:
        """Flush pending metrics and finish the underlying canonical `Run`."""
        with self._finish_lock:
            if self._finished:
                return self.data

            canonical_status = _STATUS_TO_CANONICAL.get(status, "finished")
            if self._run._pruned and canonical_status == "finished":
                canonical_status = "killed"
            try:
                self._run.finish(status=canonical_status, error=error)
                self.status = status
                self.data["status"] = status
            finally:
                self._finished = True
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
        """Upload a file artifact via canonical `Run.log_artifact()` (`/api/v1/artifacts`)."""
        p = Path(path)
        if p.is_symlink():
            raise ValueError(f"artifact path must not be a symlink: {path}")
        if not p.exists():
            raise FileNotFoundError(f"artifact path does not exist: {path}")
        if not p.is_file():
            raise ValueError(f"artifact path must be a regular file: {path}")

        meta = dict(metadata or {})
        meta.setdefault("format", format)
        _validate_finite(meta, label="metadata")

        canonical_kind = _ARTIFACT_KIND_MAP.get(kind, "file")
        artifact = self._run.log_artifact(p, name=name, kind=canonical_kind)
        if artifact is None:
            raise MatrixError(f"Failed to upload artifact {name!r}")
        return dict(artifact)

    def log_classic_eval(self, classic_eval: dict[str, Any]) -> dict[str, Any]:
        """Attach classic ML evaluation payloads via canonical `Run.log_report()`."""
        _validate_finite(classic_eval, label="classic_eval")
        self._run.log_report("custom", "classic_eval", classic_eval)
        self._run.flush(timeout=10.0)
        self.data["classic_eval"] = dict(classic_eval)
        return dict(self.data)

    def start_system_monitor(self, interval_s: float = 10.0) -> None:
        """Start background CPU/RAM/GPU telemetry via canonical `SystemMonitor`."""
        if self._run._monitor is not None:
            return
        from .system import SystemMonitor

        self._run._monitor = SystemMonitor(self._run._log_system, interval=max(0.5, float(interval_s)))
        self._run._monitor.start()


class MLOpsClient:
    """Deprecated compatibility client over canonical `tracking`, `sweeps`, and `registry`."""

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
        """Deprecated: use `matrix_sdk.start_run()` instead."""
        warnings.warn(
            "MLOpsClient.start_run() is deprecated; use matrix_sdk.start_run() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if hyperparameters is not None:
            _validate_finite(hyperparameters, label="hyperparameters")
        if hardware_info is not None:
            _validate_finite(hardware_info, label="hardware_info")

        config = dict(hyperparameters or {})
        run = canonical_start_run(
            name=name,
            tags=list(tags or ()),
            config=config or None,
            system_metrics=False,
            capture_git=False,
            capture_env=False,
            dataset_version=dataset_id,
            sweep_id=sweep_id,
            parent_run_id=parent_run_id,
            client=self._client,
            flush_interval=flush_interval,
        )
        return TrainingRun(self._client, run.data, flush_interval=flush_interval, _run=run)

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
        """Deprecated: use `matrix_sdk.sweep(config, name)` instead."""
        warnings.warn(
            "MLOpsClient.create_sweep() is deprecated; use matrix_sdk.sweep(config, name) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _validate_finite(search_space, label="search_space")
        canonical_method = "tpe" if method == "bayes" else method
        config: dict[str, Any] = {
            "method": canonical_method,
            "metric": {"key": metric_name, "goal": metric_goal},
            "parameters": _translate_search_space(search_space),
            "max_runs": int(max_trials),
        }
        early_stop = _translate_early_stopping(early_stopping)
        if early_stop is not None:
            config["early_stop"] = early_stop
        sweep_id = canonical_sweep(config, name, description=description, client=self._client)
        return {"id": sweep_id, "name": name, "method": canonical_method}

    def run_sweep_agent(
        self,
        sweep_id: str,
        train_fn: Callable[[TrainingRun, dict[str, Any]], Any],
        *,
        count: int | None = None,
    ) -> None:
        """Deprecated: use `matrix_sdk.agent(sweep_id, function, count=...)` instead."""
        warnings.warn(
            "MLOpsClient.run_sweep_agent() is deprecated; use matrix_sdk.agent(sweep_id, function, count=...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        def _wrapped() -> None:
            run = canonical_start_run(
                client=self._client,
                system_metrics=False,
                capture_git=False,
                capture_env=False,
            )
            tr = TrainingRun(self._client, run.data, _run=run)
            with tr:
                train_fn(tr, dict(run.config))

        canonical_agent(sweep_id, _wrapped, count=count, client=self._client)

    def register_model(
        self,
        name: str,
        *,
        task_type: str = "lora",
        base_model: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Deprecated: use `client.registry.register()` or `matrix_sdk.register()` instead."""
        warnings.warn(
            "MLOpsClient.register_model() is deprecated; use client.registry.register() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._client.registry.register(
            name,
            model_type=task_type,
            base_model=base_model,
            description=description,
            tags=tags,
        )

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
        """Deprecated: use `client.registry.create_version()` instead."""
        warnings.warn(
            "MLOpsClient.create_model_version() is deprecated; use client.registry.create_version() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._client.registry.create_version(
            model_id,
            artifact_id=artifact_id,
            run_id=run_id,
            lora_config=lora_config,
            signature=signature,
            metrics_snapshot=metrics_snapshot,
            stage=stage,
            eval_experiment_id=eval_experiment_id,
            safety_scan_id=safety_scan_id,
            model_card=commit_message,
        )

    def set_model_alias(self, model_id: str, alias: str, version: int) -> dict[str, Any]:
        """Deprecated: use `client.registry.set_alias()` or `matrix_sdk.set_alias()` instead."""
        warnings.warn(
            "MLOpsClient.set_model_alias() is deprecated; use client.registry.set_alias() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._client.registry.set_alias(model_id, alias, version)

    def download_model(
        self,
        model_id: str,
        *,
        alias: str = "production",
        version: int | None = None,
        dest_dir: str | Path = ".",
    ) -> Path:
        """Deprecated: use `client.registry.download()` instead."""
        warnings.warn(
            "MLOpsClient.download_model() is deprecated; use client.registry.download() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._client.registry.download(
            model_id,
            alias=alias,
            version=version,
            dest_dir=dest_dir,
        )


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
