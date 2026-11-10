"""Hugging Face `transformers` (and TRL trainers built on it): `Trainer(..., callbacks=[MatrixCallback()])`."""

from __future__ import annotations

import contextlib
import math
from typing import Any

from .. import tracking
from ..tracking import Run, resolve_run, safe_key, to_metric_value
from . import require

_tf = require("transformers", "hf")


def _perplexity(loss: float) -> float:
    try:
        return math.exp(loss)
    except OverflowError:
        return math.inf


def logs_to_metrics(logs: dict[str, Any]) -> dict[str, Any]:
    """Every numeric entry passes through unchanged (`rewards/margins`, `eval_loss`, `grad_norm`...);
    adds `perplexity` / `eval_perplexity` from `loss` / `eval_loss`."""
    out: dict[str, Any] = {}
    for key, value in logs.items():
        if isinstance(value, bool):
            value = float(value)
        try:
            out[safe_key(key)] = to_metric_value(value, key)
        except TypeError:
            continue  # strings and other non-numeric log entries
    for src, dst in (("loss", "perplexity"), ("eval_loss", "eval_perplexity")):
        value = out.get(src)
        if isinstance(value, float) and dst not in out:
            out[dst] = to_metric_value(_perplexity(value))
    return out


class MatrixCallback(_tf.TrainerCallback):
    """Forwards Trainer logs at step = `state.global_step` and records `TrainingArguments` as params.

    Without `run` (and no current run), one is started on train begin and finished on train end.
    `log_model=True` uploads `args.output_dir` (zipped) as a `model` artifact when training ends.
    """

    def __init__(self, run: Run | None = None, log_model: bool = False, **start_run_kwargs: Any) -> None:
        self._run = run
        self._owned = False
        self._log_model = log_model
        self._start_kwargs = start_run_kwargs

    @property
    def run(self) -> Run | None:
        return resolve_run(self._run)

    def on_train_begin(self, args, state, control, model=None, **kwargs):
        if not state.is_world_process_zero:
            return
        run = resolve_run(self._run)
        if run is None:
            run = tracking.start_run(**self._start_kwargs)
            self._owned = True
        self._run = run
        params: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            params.update(args.to_sanitized_dict())
        config = getattr(model, "config", None)
        if config is not None and hasattr(config, "model_type"):
            params["model_type"] = config.model_type
        if params:
            run.log_params(params)

    def on_log(self, args, state, control, logs=None, **kwargs):
        if not state.is_world_process_zero or not logs:
            return
        run = resolve_run(self._run)
        if run is None:
            return
        metrics = logs_to_metrics(logs)
        if metrics:
            run.log(metrics, step=int(state.global_step))

    def on_train_end(self, args, state, control, **kwargs):
        if not state.is_world_process_zero:
            return
        run = resolve_run(self._run)
        if run is None:
            return
        if self._log_model and args.output_dir:
            import os

            if os.path.isdir(args.output_dir) and os.listdir(args.output_dir):
                run.log_artifact(args.output_dir, name="model", kind="model")
        if self._owned:
            run.finish()
