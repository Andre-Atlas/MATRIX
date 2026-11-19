"""Classic-ML evaluation reports (classification, regression, feature importance).

Everything is computed on the client; the server only validates the shape and stores it. numpy and
scikit-learn are imported lazily, so `import matrix_sdk` stays light. Needs `pip install 'matrix-sdk[sklearn]'`
(`[shap]` for SHAP importances).

Report shapes follow contract G1 ("Relatórios tipados"):

* ClassificationReport: labels, metrics, per_class, confusion_matrix (rows true, columns predicted),
  roc / pr (one-vs-rest; binary problems have ONE curve, labelled with the positive label = `labels[1]`).
* RegressionReport: metrics, residual_hist (residual = y_true - y_pred), sample (<= 1000 points).
* ImportanceReport: method ("shap" | "permutation" | "model"), features sorted by importance, <= 100.

Curves are thinned to <= 200 points (fewer when there are many classes, to stay under the 2 MiB report
cap): consecutive duplicates and collinear points are dropped first, then points are picked at uniform
spacing along the curve's arc length, always keeping both endpoints.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from typing import Any

from .integrations import require
from .tracking import Run, resolve_run, safe_key

MAX_CURVE_POINTS = 200
MAX_CLASSES = 500
MAX_SAMPLE = 1000
MAX_FEATURES = 100
SHAP_SAMPLE = 500
PERMUTATION_SAMPLE = 2000
HIST_MIN_BINS, HIST_MAX_BINS, HIST_DEFAULT_BINS = 10, 60, 30


def _np() -> Any:
    return require("numpy", "sklearn")


def _skm() -> Any:
    return require("sklearn.metrics", "sklearn")


# -- helpers ---------------------------------------------------------------------------------------------


def _key(v: Any) -> str:
    """Label identity: 1, 1.0 and numpy ints compare equal; everything else by its string form."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _vec(np: Any, value: Any, what: str) -> Any:
    arr = np.asarray(value)
    if arr.ndim == 2 and arr.shape[1] == 1:
        arr = arr[:, 0]
    if arr.ndim != 1:
        raise ValueError(f"{what} must be 1-D, got shape {arr.shape}")
    return arr


def _check_finite(np: Any, arr: Any, what: str) -> Any:
    try:
        arr = np.asarray(arr, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be numeric") from exc
    if not np.isfinite(arr).all():
        raise ValueError(f"{what} contains NaN or infinite values; clean or impute them before evaluating")
    return arr


def _label_values(np: Any, arr: Any, what: str) -> list[Any]:
    out = arr.tolist()
    for v in out:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            raise ValueError(f"{what} contains missing values (NaN/None)")
    return out


def _finite_metrics(d: dict[str, Any]) -> dict[str, float]:
    return {k: float(v) for k, v in d.items() if v is not None and math.isfinite(float(v))}


def downsample_indices(x: Any, y: Any, max_points: int = MAX_CURVE_POINTS) -> Any:
    """Indices of the points to keep (sorted, endpoints included, at most `max_points`)."""
    np = _np()
    pts = np.column_stack([np.asarray(x, dtype=float), np.asarray(y, dtype=float)])
    n = len(pts)
    if n <= 2:
        return np.arange(n)
    idx = np.arange(n)
    # 1. consecutive duplicates
    keep = np.ones(n, dtype=bool)
    keep[1:] = np.any(np.diff(pts, axis=0) != 0, axis=1)
    idx, pts = idx[keep], pts[keep]
    # 2. collinear interior points (same direction)
    if len(pts) > 2:
        d1, d2 = pts[1:-1] - pts[:-2], pts[2:] - pts[1:-1]
        cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        dot = (d1 * d2).sum(axis=1)
        keep = np.ones(len(pts), dtype=bool)
        keep[1:-1] = ~((np.abs(cross) <= 1e-12) & (dot > 0))
        idx, pts = idx[keep], pts[keep]
    if len(pts) <= max_points:
        return idx
    # 3. uniform thinning by arc length
    seg = np.sqrt((np.diff(pts, axis=0) ** 2).sum(axis=1))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    targets = np.linspace(0.0, s[-1], max_points)
    pos = np.clip(np.searchsorted(s, targets), 1, len(s) - 1)
    pick = np.where(np.abs(s[pos] - targets) < np.abs(s[pos - 1] - targets), pos, pos - 1)
    pick[0], pick[-1] = 0, len(pts) - 1
    return idx[np.unique(pick)]


def _thin(np: Any, a: Any, b: Any, max_points: int) -> tuple[list[float], list[float]]:
    sel = downsample_indices(a, b, max_points)
    return (np.round(np.asarray(a, dtype=float)[sel], 6).tolist(),
            np.round(np.asarray(b, dtype=float)[sel], 6).tolist())


# -- classification --------------------------------------------------------------------------------------


def classification_report_data(y_true: Any, y_score: Any = None, y_pred: Any = None,
                               labels: Sequence[Any] | None = None) -> dict[str, Any]:
    """ClassificationReport dict.

    `y_score`: probabilities, shape (n,) for the positive class of a binary problem (the positive class is
    `labels[1]`, i.e. the larger label when `labels` is not given) or (n, k) with columns in `labels` order
    (a binary (n, 2) array is reduced to column 1). `y_pred` defaults to the argmax of `y_score`
    (threshold 0.5 for binary). With only `y_pred`, ROC/PR curves, roc_auc, average_precision and log_loss
    are omitted.
    """
    np = _np()
    sk = _skm()
    if y_score is None and y_pred is None:
        raise ValueError("pass y_score (probabilities) and/or y_pred")
    yt = _vec(np, y_true, "y_true")
    n = len(yt)
    if n == 0:
        raise ValueError("y_true is empty")
    yt_vals = _label_values(np, yt, "y_true")
    yp_vals = None
    if y_pred is not None:
        yp = _vec(np, y_pred, "y_pred")
        if len(yp) != n:
            raise ValueError(f"y_pred has {len(yp)} rows, y_true has {n}")
        yp_vals = _label_values(np, yp, "y_pred")
    score = None
    if y_score is not None:
        score = _check_finite(np, y_score, "y_score")
        if score.ndim == 2 and score.shape[1] == 1:
            score = score[:, 0]
        if score.ndim not in (1, 2) or len(score) != n:
            raise ValueError(f"y_score must have shape ({n},) or ({n}, k), got {score.shape}")

    # labels
    if labels is not None:
        label_vals = list(labels.tolist() if hasattr(labels, "tolist") else labels)
    else:
        seen: dict[str, Any] = {}
        for v in yt_vals + (yp_vals or []):
            seen.setdefault(_key(v), v)
        try:
            label_vals = sorted(seen.values())
        except TypeError:
            label_vals = sorted(seen.values(), key=_key)
    index = {_key(v): i for i, v in enumerate(label_vals)}
    if len(index) != len(label_vals):
        raise ValueError("labels must be unique")
    k = len(label_vals)
    if k > MAX_CLASSES:
        raise ValueError(f"{k} classes is too many for a classification report (limit {MAX_CLASSES}: the "
                         "confusion matrix and curves would exceed the 2 MiB report cap); evaluate the top "
                         "classes or log a summary table instead")
    names = [str(v) for v in label_vals]

    def to_idx(vals: list[Any], what: str) -> Any:
        try:
            return np.array([index[_key(v)] for v in vals], dtype=int)
        except KeyError as exc:
            raise ValueError(f"{what} has label {exc.args[0]!r} that is not in labels={names}") from None

    ti = to_idx(yt_vals, "y_true")

    # scores -> (n,) positive-class vector (binary) or (n, k)
    binary = k == 2
    pos_score = None
    proba = None
    if score is not None:
        if score.ndim == 2 and score.shape[1] == 2 and binary:
            score = score[:, 1]
        if score.ndim == 1:
            if k == 2:
                pos_score = score
            elif k == 1:
                warnings.warn("only one class present: y_score ignored (ROC/PR/log_loss undefined)",
                              UserWarning, stacklevel=3)
                score = None
            else:
                raise ValueError(f"y_score is 1-D but there are {k} classes; pass an (n, {k}) array")
        else:
            if score.shape[1] != k:
                raise ValueError(f"y_score has {score.shape[1]} columns but there are {k} labels {names}; "
                                 "pass labels= in the column order of y_score")
            proba = score
        if score is not None and (score.min() < -1e-9 or score.max() > 1 + 1e-9):
            raise ValueError("y_score must be probabilities in [0, 1] (apply softmax/sigmoid first)")

    if yp_vals is not None:
        pi = to_idx(yp_vals, "y_pred")
    elif pos_score is not None:
        pi = (pos_score >= 0.5).astype(int)
    elif proba is not None:
        pi = proba.argmax(axis=1)
    else:  # k == 1 with scores dropped and no y_pred
        raise ValueError("y_pred is required when y_score cannot be used")

    cm = np.zeros((k, k), dtype=np.int64)
    np.add.at(cm, (ti, pi), 1)
    all_idx = list(range(k))
    prec, rec, f1, support = sk.precision_recall_fscore_support(ti, pi, labels=all_idx, zero_division=0)
    support = cm.sum(axis=1)
    metrics: dict[str, Any] = {"accuracy": float((ti == pi).mean())}
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        metrics["balanced_accuracy"] = sk.balanced_accuracy_score(ti, pi)
        metrics["matthews_corrcoef"] = sk.matthews_corrcoef(ti, pi)
        metrics["cohen_kappa"] = sk.cohen_kappa_score(ti, pi, labels=all_idx)
        for avg in ("macro", "weighted"):
            p, r, f, _ = sk.precision_recall_fscore_support(ti, pi, labels=all_idx, average=avg,
                                                            zero_division=0)
            metrics[f"precision_{avg}"], metrics[f"recall_{avg}"], metrics[f"f1_{avg}"] = p, r, f

    roc: list[dict[str, Any]] = []
    pr: list[dict[str, Any]] = []
    if pos_score is not None or proba is not None:
        points = MAX_CURVE_POINTS if k <= 8 else max(25, 1600 // k)
        targets = [(1, pos_score)] if pos_score is not None else [(c, proba[:, c]) for c in range(k)]
        aucs, aps = [], []
        for c, sc in targets:
            pos = ti == c
            n_pos = int(pos.sum())
            if n_pos == 0 or n_pos == n:
                warnings.warn(f"class {names[c]!r} is absent or the only class in y_true: ROC curve and "
                              "AUC are undefined and were omitted", UserWarning, stacklevel=3)
            else:
                fpr, tpr, _ = sk.roc_curve(pos, sc)
                auc = float(sk.roc_auc_score(pos, sc))
                a, b = _thin(np, fpr, tpr, points)
                roc.append({"label": names[c], "fpr": a, "tpr": b, "auc": auc})
                aucs.append(auc)
            if n_pos > 0:
                p_, r_, _ = sk.precision_recall_curve(pos, sc)
                ap = float(sk.average_precision_score(pos, sc))
                a, b = _thin(np, p_, r_, points)
                pr.append({"label": names[c], "precision": a, "recall": b, "ap": ap})
                aps.append(ap)
        if aucs:
            metrics["roc_auc"] = float(np.mean(aucs))
        if aps:
            metrics["average_precision"] = float(np.mean(aps))
        eps = 1e-15
        if pos_score is not None:
            p1 = np.clip(pos_score, eps, 1 - eps)
            metrics["log_loss"] = float(-np.mean(np.where(ti == 1, np.log(p1), np.log(1 - p1))))
        else:
            rows = proba / np.clip(proba.sum(axis=1, keepdims=True), eps, None)
            metrics["log_loss"] = float(-np.mean(np.log(np.clip(rows[np.arange(n), ti], eps, 1.0))))

    return {
        "labels": names,
        "metrics": _finite_metrics(metrics),
        "per_class": [{"label": names[i], "precision": float(prec[i]), "recall": float(rec[i]),
                       "f1": float(f1[i]), "support": int(support[i])} for i in range(k)],
        "confusion_matrix": cm.tolist(),
        "roc": roc,
        "pr": pr,
    }


def _log_headline(run: Run, name: str, metrics: dict[str, float]) -> None:
    run.log({safe_key(f"eval/{name}/{k}"): v for k, v in metrics.items()})


def _need_run(run: Run | None) -> Run:
    r = resolve_run(run)
    if r is None:
        raise RuntimeError("no active run: pass run= or call matrix_sdk.start_run() first")
    return r


def log_classification_report(run: Run | None, y_true: Any, y_score: Any = None, y_pred: Any = None,
                              labels: Sequence[Any] | None = None, name: str = "eval",
                              log_metrics: bool = True) -> dict[str, Any]:
    """Computes the ClassificationReport, logs it as report `name` and (default) its metrics as
    `eval/<name>/<metric>` at the run's next step. Returns the report data."""
    r = _need_run(run)
    data = classification_report_data(y_true, y_score, y_pred, labels)
    r.log_report("classification", name, data)
    if log_metrics:
        _log_headline(r, name, data["metrics"])
    return data


# -- regression ------------------------------------------------------------------------------------------


def regression_report_data(y_true: Any, y_pred: Any, seed: int = 0) -> dict[str, Any]:
    np = _np()
    sk = _skm()
    yt, yp = (_check_finite(np, a, w) for a, w in ((y_true, "y_true"), (y_pred, "y_pred")))
    for arr, what in ((yt, "y_true"), (yp, "y_pred")):
        if arr.ndim == 2 and arr.shape[1] == 1:
            arr.shape = (len(arr),)
        if arr.ndim != 1:
            raise ValueError(f"{what} must be 1-D (multioutput regression is not supported), "
                             f"got shape {arr.shape}")
    yt, yp = yt.reshape(-1), yp.reshape(-1)
    if len(yt) != len(yp):
        raise ValueError(f"y_pred has {len(yp)} rows, y_true has {len(yt)}")
    n = len(yt)
    if n < 2:
        raise ValueError("regression report needs at least 2 samples")
    res = yt - yp
    metrics: dict[str, Any] = {
        "rmse": math.sqrt(float(np.mean(res ** 2))),
        "mae": float(np.mean(np.abs(res))),
        "r2": float(sk.r2_score(yt, yp)),
        "median_ae": float(np.median(np.abs(res))),
        "explained_variance": float(sk.explained_variance_score(yt, yp)),
        "max_error": float(np.max(np.abs(res))),
    }
    if not np.any(yt == 0):
        metrics["mape"] = float(np.mean(np.abs(res / yt)))
    q75, q25 = np.percentile(res, [75, 25])
    width = 2 * (q75 - q25) * n ** (-1 / 3)
    span = float(res.max() - res.min())
    bins = HIST_DEFAULT_BINS if width <= 0 or span <= 0 else math.ceil(span / width)
    bins = max(HIST_MIN_BINS, min(HIST_MAX_BINS, bins))
    lo, hi = float(res.min()), float(res.max())
    if hi - lo <= 1e-9 * max(1.0, abs(lo), abs(hi)):  # constant residuals: give the bins a finite width
        lo, hi = lo - 0.5, hi + 0.5
    counts, edges = np.histogram(res, bins=bins, range=(lo, hi))
    sel = np.arange(n)
    if n > MAX_SAMPLE:
        sel = np.sort(np.random.default_rng(seed).choice(n, MAX_SAMPLE, replace=False))
    return {
        "metrics": _finite_metrics(metrics),
        "residual_hist": {"edges": edges.tolist(), "counts": counts.tolist()},
        "sample": {"y_true": yt[sel].tolist(), "y_pred": yp[sel].tolist()},
    }


def log_regression_report(run: Run | None, y_true: Any, y_pred: Any, name: str = "eval",
                          log_metrics: bool = True) -> dict[str, Any]:
    r = _need_run(run)
    data = regression_report_data(y_true, y_pred)
    r.log_report("regression", name, data)
    if log_metrics:
        _log_headline(r, name, data["metrics"])
    return data


# -- feature importance ----------------------------------------------------------------------------------


def _is_tree_model(model: Any) -> bool:
    if type(model).__module__.split(".")[0] in ("xgboost", "lightgbm", "catboost"):
        return True
    try:
        from sklearn.ensemble import BaseEnsemble, GradientBoostingClassifier, GradientBoostingRegressor
        from sklearn.tree import BaseDecisionTree

        if isinstance(model, (GradientBoostingClassifier, GradientBoostingRegressor, BaseDecisionTree)):
            return True
        if isinstance(model, BaseEnsemble):
            return all(isinstance(e, BaseDecisionTree) for e in getattr(model, "estimators_", [None]))
    except ImportError:  # pragma: no cover
        pass
    return False


def _rows(X: Any, idx: Any) -> Any:
    return X.iloc[idx] if hasattr(X, "iloc") else X[idx]


def _shap_importance(np: Any, model: Any, X: Any, n_features: int, seed: int) -> Any:
    shap = require("shap", "shap")
    if not _is_tree_model(model):
        raise ValueError("method='shap' supports tree models only (random forests, gradient boosting, "
                         "xgboost, lightgbm); use method='permutation' for this model")
    n = len(X)
    if n > SHAP_SAMPLE:
        X = _rows(X, np.sort(np.random.default_rng(seed).choice(n, SHAP_SAMPLE, replace=False)))
    try:
        values = shap.TreeExplainer(model).shap_values(X, check_additivity=False)
    except TypeError:
        values = shap.TreeExplainer(model).shap_values(X)
    arr = np.abs(np.stack([np.asarray(v) for v in values], axis=-1) if isinstance(values, list)
                 else np.asarray(values))
    if arr.ndim == 1:
        arr = arr[None, :]
    axis = 1 if arr.ndim > 1 and arr.shape[1] == n_features else next(i for i, s in enumerate(arr.shape)
                                                                   if s == n_features)
    return np.moveaxis(arr, axis, 0).reshape(n_features, -1).mean(axis=1)


def _model_importance(np: Any, model: Any, n_features: int) -> Any | None:
    imp = getattr(model, "feature_importances_", None)
    if imp is None and getattr(model, "coef_", None) is not None:
        coef = np.abs(np.asarray(model.coef_, dtype=float))
        imp = coef.mean(axis=0) if coef.ndim > 1 else coef
    if imp is None:
        return None
    imp = np.asarray(imp, dtype=float).reshape(-1)
    if len(imp) != n_features:
        raise ValueError(f"model reports {len(imp)} importances but X has {n_features} features")
    return imp


def feature_importance_data(model: Any, X: Any, y: Any = None, feature_names: Sequence[str] | None = None,
                            method: str = "auto", n_repeats: int = 5, random_state: int = 0,
                            max_features: int = MAX_FEATURES) -> dict[str, Any]:
    """ImportanceReport dict. `method`: auto | shap | model | permutation (see module docs)."""
    np = _np()
    if method not in ("auto", "shap", "model", "permutation"):
        raise ValueError("method must be one of auto, shap, model, permutation")
    shape = getattr(X, "shape", None) or np.asarray(X).shape
    if len(shape) != 2:
        raise ValueError(f"X must be 2-D, got shape {tuple(shape)}")
    n_features = int(shape[1])
    if feature_names is not None:
        names = [str(f) for f in feature_names]
    elif hasattr(X, "columns"):
        names = [str(c) for c in X.columns]
    else:
        names = [f"f{i}" for i in range(n_features)]
    if len(names) != n_features:
        raise ValueError(f"{len(names)} feature names for {n_features} features")
    if y is not None and len(y) != shape[0]:
        raise ValueError(f"y has {len(y)} rows, X has {shape[0]}")

    used = None
    imp = std = None
    if method in ("auto", "shap"):
        shap_ok = True
        if method == "auto":
            try:
                require("shap", "shap")
            except ImportError:
                shap_ok = False
            shap_ok = shap_ok and _is_tree_model(model)
        if shap_ok:
            try:
                imp, used = _shap_importance(np, model, X, n_features, random_state), "shap"
            except Exception as exc:
                if method == "shap":
                    raise
                warnings.warn(f"SHAP importance failed ({exc}); falling back", UserWarning, stacklevel=2)
    if imp is None and method in ("auto", "model"):
        imp = _model_importance(np, model, n_features)
        if imp is not None:
            used = "model"
        elif method == "model":
            raise ValueError(f"{type(model).__name__} has no feature_importances_ or coef_; "
                             "use method='permutation' with y")
    if imp is None:
        if y is None:
            raise ValueError("no importance available from the model; pass y= to use permutation importance")
        from sklearn.inspection import permutation_importance

        Xp, yp = X, np.asarray(y)
        if len(yp) > PERMUTATION_SAMPLE:
            sel = np.sort(np.random.default_rng(random_state).choice(len(yp), PERMUTATION_SAMPLE,
                                                                     replace=False))
            Xp, yp = _rows(X, sel), yp[sel]
        res = permutation_importance(model, Xp, yp, n_repeats=n_repeats, random_state=random_state)
        imp, std, used = res.importances_mean, res.importances_std, "permutation"

    order = np.argsort(-np.asarray(imp), kind="stable")[: max(1, min(int(max_features), MAX_FEATURES))]
    feats = []
    for i in order:
        f: dict[str, Any] = {"name": names[i], "importance": float(imp[i])}
        if std is not None:
            f["std"] = float(std[i])
        feats.append(f)
    return {"method": used, "features": feats}


def log_feature_importance(run: Run | None, model: Any, X: Any, y: Any = None,
                           feature_names: Sequence[str] | None = None, method: str = "auto",
                           n_repeats: int = 5, random_state: int = 0, max_features: int = MAX_FEATURES,
                           name: str = "importance") -> dict[str, Any]:
    r = _need_run(run)
    data = feature_importance_data(model, X, y, feature_names, method, n_repeats, random_state, max_features)
    r.log_report("importance", name, data)
    return data


# -- convenience -----------------------------------------------------------------------------------------


def evaluate_and_log(run: Run | None, model: Any, X_test: Any, y_test: Any, task: str = "auto",
                     name: str = "eval", importance: bool = True) -> dict[str, Any]:
    """Predicts with `model`, logs the classification/regression report and the feature importance
    (SHAP for tree models when installed, otherwise permutation). Returns {"task", "report", "importance"}."""
    r = _need_run(run)
    if task not in ("auto", "classification", "regression"):
        raise ValueError("task must be auto, classification or regression")
    if task == "auto":
        from sklearn.base import is_classifier

        task = "classification" if is_classifier(model) else "regression"
    if task == "classification":
        if hasattr(model, "predict_proba"):
            report = log_classification_report(r, y_test, y_score=model.predict_proba(X_test),
                                               labels=list(model.classes_), name=name)
        else:
            report = log_classification_report(r, y_test, y_pred=model.predict(X_test),
                                               labels=getattr(model, "classes_", None), name=name)
    else:
        report = log_regression_report(r, y_test, model.predict(X_test), name=name)
    out: dict[str, Any] = {"task": task, "report": report, "importance": None}
    if importance:
        try:
            use_shap = _is_tree_model(model)
            if use_shap:
                try:
                    require("shap", "shap")
                except ImportError:
                    use_shap = False
            out["importance"] = log_feature_importance(r, model, X_test, y_test,
                                                       method="shap" if use_shap else "permutation")
        except Exception as exc:  # noqa: BLE001 - importance is optional, never break the evaluation
            warnings.warn(f"feature importance skipped: {exc}", UserWarning, stacklevel=2)
    return out
