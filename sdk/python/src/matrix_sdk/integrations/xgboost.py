"""XGBoost: `xgb.train(..., callbacks=[MatrixCallback(run)])` logs every eval metric per iteration."""

from __future__ import annotations

from typing import Any

from ..tracking import Run, resolve_run, safe_key, to_metric_value
from . import require

_xgb = require("xgboost", "xgboost")


class MatrixCallback(_xgb.callback.TrainingCallback):
    """Logs `<data>/<metric>` (e.g. `validation_0/logloss`) at step = boosting iteration."""

    def __init__(self, run: Run | None = None) -> None:
        super().__init__()
        self._run = run

    def after_iteration(self, model: Any, epoch: int, evals_log: dict[str, dict[str, list]]) -> bool:
        run = resolve_run(self._run)
        if run is None:
            return False
        metrics: dict[str, Any] = {}
        for data, by_metric in evals_log.items():
            for metric, values in by_metric.items():
                if not values:
                    continue
                last = values[-1]
                if isinstance(last, tuple):  # cv: (mean, std)
                    last = last[0]
                try:
                    metrics[safe_key(f"{data}/{metric}")] = to_metric_value(last)
                except TypeError:
                    continue
        if metrics:
            run.log(metrics, step=int(epoch))
        return False
