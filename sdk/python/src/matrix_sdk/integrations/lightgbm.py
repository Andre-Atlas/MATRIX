"""LightGBM: `lgb.train(..., callbacks=[callback(run)])` logs every eval metric per iteration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..tracking import Run, resolve_run, safe_key, to_metric_value
from . import require

require("lightgbm", "lightgbm")


def callback(run: Run | None = None) -> Callable[[Any], None]:
    """Logs `<data>/<metric>` (e.g. `valid_0/l2`) at step = iteration."""

    def _callback(env: Any) -> None:
        target = resolve_run(run)
        if target is None:
            return
        metrics: dict[str, Any] = {}
        for item in env.evaluation_result_list or []:
            data, metric, value = item[0], item[1], item[2]
            try:
                metrics[safe_key(f"{data}/{metric}")] = to_metric_value(value)
            except TypeError:
                continue
        if metrics:
            target.log(metrics, step=int(env.iteration))

    _callback.order = 30  # after LightGBM's own record_evaluation / early stopping
    return _callback
