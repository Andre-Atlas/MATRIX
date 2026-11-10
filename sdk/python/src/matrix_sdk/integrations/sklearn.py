"""scikit-learn autologging: `autolog(run)` before fitting, `autolog(disable=True)` to undo.

`BaseEstimator` itself defines no `fit`, so every `fit` defined on a `BaseEstimator` subclass that is
known when `autolog()` runs (all of scikit-learn plus already-imported user classes) is wrapped once.
Only the outermost `fit` is logged: a forest's trees or a pipeline's steps do not produce extra entries.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from typing import Any

from ..tracking import Run, resolve_run, to_metric_value
from . import require

log = logging.getLogger("matrix_sdk.integrations.sklearn")
_sk_base = require("sklearn.base", "sklearn")

_patched: dict[type, Any] = {}
_state: dict[str, Any] = {"run": None, "enabled": False}
_local = threading.local()
_lock = threading.Lock()


def _subclasses(cls: type) -> list[type]:
    seen: list[type] = []
    stack = [cls]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.append(sub)
                stack.append(sub)
    return seen


def _record(est: Any, X: Any, y: Any, seconds: float) -> None:
    run = resolve_run(_state["run"])
    if run is None:
        return
    try:
        params = {"estimator": type(est).__name__, **est.get_params(deep=False)}
        run.log_params(params)
    except Exception:
        log.debug("get_params failed", exc_info=True)
    metrics: dict[str, Any] = {"fit_time_s": seconds}
    if X is not None and y is not None and callable(getattr(est, "score", None)):
        try:
            metrics["train_score"] = to_metric_value(est.score(X, y))
        except Exception:
            log.debug("train score failed", exc_info=True)
    run.log(metrics)


def _wrap(original: Any) -> Any:
    @functools.wraps(original)
    def fit(self, *args, **kwargs):
        depth = getattr(_local, "depth", 0)
        if depth or not _state["enabled"]:
            return original(self, *args, **kwargs)
        _local.depth = depth + 1
        try:
            start = time.perf_counter()
            result = original(self, *args, **kwargs)
            seconds = time.perf_counter() - start
        finally:
            _local.depth = depth
        X = args[0] if args else kwargs.get("X")
        y = args[1] if len(args) > 1 else kwargs.get("y")
        _record(self, X, y, seconds)
        return result

    fit.__matrix_original__ = original
    return fit


def autolog(run: Run | None = None, disable: bool = False) -> None:
    """Idempotent: calling again only changes the target run. Without `run`, the current run is used."""
    with _lock:
        if disable:
            for cls, original in _patched.items():
                cls.fit = original
            _patched.clear()
            _state.update(run=None, enabled=False)
            return
        _state["run"] = run
        _state["enabled"] = True
        try:
            from sklearn.utils import all_estimators

            all_estimators()  # imports every scikit-learn estimator module
        except Exception:
            log.debug("all_estimators failed", exc_info=True)
        for cls in _subclasses(_sk_base.BaseEstimator):
            original = cls.__dict__.get("fit")
            if original is None or cls in _patched or not callable(original):
                continue
            if hasattr(original, "__matrix_original__"):
                continue
            try:
                cls.fit = _wrap(original)
            except (TypeError, AttributeError):  # extension types
                continue
            _patched[cls] = original


def is_enabled() -> bool:
    return bool(_state["enabled"])
