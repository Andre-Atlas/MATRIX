"""Pure-Python hyperparameter sweep engine, early stopping evaluator, and sweep analysis."""

from __future__ import annotations

import itertools
import math
import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_VALID_METHODS = {"grid", "random", "bayes"}
_VALID_GOALS = {"minimize", "maximize"}
_VALID_STRATEGIES = {"none", "median", "asha"}


class InvalidSearchSpaceError(ValueError):
    """Raised when a sweep search_space definition is malformed."""


class SweepExhaustedError(Exception):
    """Raised when a sweep has no remaining trials to suggest."""


@dataclass(frozen=True)
class EarlyStopDecision:
    """Result of evaluating an early stopping rule on a running trial."""

    should_stop: bool
    reason: str | None = None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_search_space(search_space: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate and normalize a sweep search_space dictionary."""
    if not isinstance(search_space, Mapping) or len(search_space) == 0:
        raise InvalidSearchSpaceError("search_space must be a non-empty mapping")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_spec in search_space.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise InvalidSearchSpaceError("Parameter names must be non-empty strings")
        if not isinstance(raw_spec, Mapping):
            raise InvalidSearchSpaceError(f"Parameter '{raw_name}' spec must be a mapping")

        ptype = raw_spec.get("type")
        if ptype is None:
            if "values" in raw_spec:
                ptype = "categorical"
            elif "min" in raw_spec and "max" in raw_spec:
                step_val = raw_spec.get("step")
                if (
                    _is_int(raw_spec["min"])
                    and _is_int(raw_spec["max"])
                    and (step_val is None or _is_int(step_val))
                ):
                    ptype = "int"
                else:
                    ptype = "float"
            else:
                raise InvalidSearchSpaceError(
                    f"Parameter '{raw_name}' must define 'type', 'values', or ('min', 'max')"
                )

        if ptype == "categorical":
            values = raw_spec.get("values")
            if not isinstance(values, (list, tuple)) or len(values) == 0:
                raise InvalidSearchSpaceError(
                    f"Categorical parameter '{raw_name}' must define a non-empty 'values' list"
                )
            normalized[raw_name] = {"type": "categorical", "values": list(values)}

        elif ptype == "int":
            min_v = raw_spec.get("min")
            max_v = raw_spec.get("max")
            step_v = raw_spec.get("step", 1)
            log_v = bool(raw_spec.get("log", False))
            if not _is_int(min_v) or not _is_int(max_v):
                raise InvalidSearchSpaceError(f"Int parameter '{raw_name}' requires integer min and max")
            if not _is_int(step_v) or step_v < 1:
                raise InvalidSearchSpaceError(f"Int parameter '{raw_name}' step must be an integer >= 1")
            if min_v > max_v:
                raise InvalidSearchSpaceError(f"Int parameter '{raw_name}' requires min <= max")
            if log_v and min_v <= 0:
                raise InvalidSearchSpaceError(f"Int parameter '{raw_name}' with log=True requires min > 0")
            normalized[raw_name] = {
                "type": "int",
                "min": int(min_v),
                "max": int(max_v),
                "step": int(step_v),
                "log": log_v,
            }

        elif ptype == "float":
            min_v = raw_spec.get("min")
            max_v = raw_spec.get("max")
            log_v = bool(raw_spec.get("log", False))
            if not _is_number(min_v) or not _is_number(max_v):
                raise InvalidSearchSpaceError(f"Float parameter '{raw_name}' requires numeric min and max")
            fmin = float(min_v)
            fmax = float(max_v)
            if not (math.isfinite(fmin) and math.isfinite(fmax)):
                raise InvalidSearchSpaceError(f"Float parameter '{raw_name}' bounds must be finite")
            if fmin > fmax:
                raise InvalidSearchSpaceError(f"Float parameter '{raw_name}' requires min <= max")
            if log_v and fmin <= 0.0:
                raise InvalidSearchSpaceError(f"Float parameter '{raw_name}' with log=True requires min > 0")
            spec_out: dict[str, Any] = {
                "type": "float",
                "min": fmin,
                "max": fmax,
                "log": log_v,
            }
            if "values" in raw_spec and isinstance(raw_spec["values"], (list, tuple)):
                vals: list[float] = []
                for v in raw_spec["values"]:
                    if not _is_number(v) or not math.isfinite(float(v)):
                        raise InvalidSearchSpaceError(
                            f"Float parameter '{raw_name}' values must be finite numbers"
                        )
                    vals.append(float(v))
                spec_out["values"] = vals
            normalized[raw_name] = spec_out

        else:
            raise InvalidSearchSpaceError(f"Unsupported parameter type '{ptype}' for '{raw_name}'")

    return normalized


def _make_rng(seed: int | str | None, trial_count: int) -> random.Random:
    if seed is None:
        return random.Random()
    if _is_int(seed):
        return random.Random(int(seed) + trial_count)
    return random.Random(f"{seed}:{trial_count}")


def _extract_trial_params(trial: Any) -> dict[str, Any]:
    if isinstance(trial, Mapping):
        if "hyperparams" in trial and isinstance(trial["hyperparams"], Mapping):
            return dict(trial["hyperparams"])
        if "params" in trial and isinstance(trial["params"], Mapping):
            return dict(trial["params"])
        return dict(trial)
    hp = getattr(trial, "hyperparams", None)
    if isinstance(hp, Mapping):
        return dict(hp)
    return {}


def _extract_trial_metric(trial: Any, metric_name: str | None = None) -> float | None:
    if isinstance(trial, Mapping):
        for key in ("metric_value", "best_metric_value", "value"):
            val = trial.get(key)
            if _is_number(val) and math.isfinite(float(val)):
                return float(val)
        summary = trial.get("summary_metrics")
    else:
        for attr in ("metric_value", "best_metric_value", "value"):
            val = getattr(trial, attr, None)
            if _is_number(val) and math.isfinite(float(val)):
                return float(val)
        summary = getattr(trial, "summary_metrics", None)

    if isinstance(summary, Mapping):
        if metric_name and metric_name in summary:
            val = summary[metric_name]
            if _is_number(val) and math.isfinite(float(val)):
                return float(val)
        if len(summary) == 1:
            only_val = next(iter(summary.values()))
            if _is_number(only_val) and math.isfinite(float(only_val)):
                return float(only_val)
    return None


def _grid_values_for_param(spec: dict[str, Any]) -> list[Any]:
    ptype = spec["type"]
    if ptype == "categorical":
        return list(spec["values"])
    if ptype == "int":
        return list(range(spec["min"], spec["max"] + 1, spec["step"]))
    # float
    if spec.get("values"):
        return list(spec["values"])
    fmin, fmax = spec["min"], spec["max"]
    if math.isclose(fmin, fmax):
        return [fmin]
    n_points = 5
    if spec["log"]:
        lo, hi = math.log(fmin), math.log(fmax)
        return [math.exp(lo + (hi - lo) * i / (n_points - 1)) for i in range(n_points)]
    return [fmin + (fmax - fmin) * i / (n_points - 1) for i in range(n_points)]


def _params_match(candidate: dict[str, Any], existing: dict[str, Any], space: dict[str, dict[str, Any]]) -> bool:
    for name, spec in space.items():
        if name not in existing:
            return False
        c_val = candidate[name]
        e_val = existing[name]
        if spec["type"] == "float":
            if not _is_number(e_val) or not math.isclose(float(c_val), float(e_val), rel_tol=1e-9, abs_tol=1e-12):
                return False
        else:
            if c_val != e_val:
                return False
    return True


def _sample_param(spec: dict[str, Any], rng: random.Random) -> Any:
    ptype = spec["type"]
    if ptype == "categorical":
        return rng.choice(spec["values"])
    if ptype == "int":
        min_v, max_v, step = spec["min"], spec["max"], spec["step"]
        if min_v == max_v:
            return min_v
        if spec["log"]:
            raw = math.exp(rng.uniform(math.log(min_v), math.log(max_v)))
            snapped = min_v + round((raw - min_v) / step) * step
            return max(min_v, min(max_v, int(snapped)))
        count = (max_v - min_v) // step
        return min_v + rng.randint(0, count) * step
    # float
    fmin, fmax = spec["min"], spec["max"]
    if math.isclose(fmin, fmax):
        return fmin
    if spec["log"]:
        return math.exp(rng.uniform(math.log(fmin), math.log(fmax)))
    return rng.uniform(fmin, fmax)


def _sample_random(space: dict[str, dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    return {name: _sample_param(spec, rng) for name, spec in space.items()}


def _to_working_coord(value: Any, spec: dict[str, Any]) -> float:
    fval = float(value)
    if spec.get("log"):
        fval = max(fval, 1e-12)
        return math.log(fval)
    return fval


def _kde_log_density_numeric(
    candidate_val: Any,
    group_vals: Sequence[Any],
    spec: dict[str, Any],
) -> float:
    z_min = _to_working_coord(spec["min"], spec)
    z_max = _to_working_coord(spec["max"], spec)
    span = max(z_max - z_min, 1e-9)
    z_cand = _to_working_coord(candidate_val, spec)
    z_obs = [_to_working_coord(v, spec) for v in group_vals]

    n = len(z_obs)
    if n == 0:
        return -math.log(span)

    if n > 1:
        std = statistics.stdev(z_obs)
        bw = max(1.06 * std * (n ** -0.2), span * 0.05, 1e-6)
    else:
        bw = max(span * 0.15, 1e-6)

    inv_norm = 1.0 / (bw * math.sqrt(2.0 * math.pi))
    kernel_sum = 0.0
    for z_j in z_obs:
        diff = (z_cand - z_j) / bw
        kernel_sum += math.exp(-0.5 * diff * diff) * inv_norm

    # Mix with small uniform prior so density is strictly positive everywhere.
    prior = 0.05 / span
    density = 0.95 * (kernel_sum / n) + prior
    return math.log(max(density, 1e-300))


def _categorical_log_prob(candidate_val: Any, group_vals: Sequence[Any], choices: Sequence[Any]) -> float:
    alpha = 1.0
    count = sum(1 for v in group_vals if v == candidate_val)
    prob = (count + alpha) / (len(group_vals) + alpha * max(len(choices), 1))
    return math.log(max(prob, 1e-300))


def _suggest_bayes(
    space: dict[str, dict[str, Any]],
    trials: Sequence[Any],
    metric_goal: str,
    rng: random.Random,
    *,
    metric_name: str | None = None,
    gamma: float = 0.25,
    n_candidates: int = 64,
    warmup_trials: int = 5,
) -> dict[str, Any]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for t in trials:
        m = _extract_trial_metric(t, metric_name=metric_name)
        if m is not None:
            scored.append((m, _extract_trial_params(t)))

    if len(scored) < warmup_trials:
        return _sample_random(space, rng)

    reverse = metric_goal == "maximize"
    scored.sort(key=lambda item: item[0], reverse=reverse)

    n_good = max(1, min(len(scored) - 1, math.ceil(len(scored) * gamma)))
    good_params = [p for _, p in scored[:n_good]]
    bad_params = [p for _, p in scored[n_good:]]

    best_candidate: dict[str, Any] | None = None
    best_score = -float("inf")

    for _ in range(n_candidates):
        cand = _sample_random(space, rng)
        log_ratio = 0.0
        for name, spec in space.items():
            g_vals = [p[name] for p in good_params if name in p]
            b_vals = [p[name] for p in bad_params if name in p]
            if spec["type"] == "categorical":
                l_log = _categorical_log_prob(cand[name], g_vals, spec["values"])
                g_log = _categorical_log_prob(cand[name], b_vals, spec["values"])
            else:
                l_log = _kde_log_density_numeric(cand[name], g_vals, spec)
                g_log = _kde_log_density_numeric(cand[name], b_vals, spec)
            log_ratio += l_log - g_log

        if log_ratio > best_score:
            best_score = log_ratio
            best_candidate = cand

    return best_candidate if best_candidate is not None else _sample_random(space, rng)


def suggest_next_params(
    method: str,
    search_space: Mapping[str, Any],
    completed_or_existing_trials: Sequence[Any],
    metric_goal: str = "minimize",
    seed: int | str | None = None,
    max_trials: int | None = None,
    metric_name: str | None = None,
) -> dict[str, Any]:
    """Suggest the next hyperparameter configuration for a sweep."""
    if method not in _VALID_METHODS:
        raise ValueError(f"Unsupported sweep method '{method}'; expected one of {sorted(_VALID_METHODS)}")
    if metric_goal not in _VALID_GOALS:
        raise ValueError(f"Unsupported metric_goal '{metric_goal}'; expected one of {sorted(_VALID_GOALS)}")

    space = validate_search_space(search_space)
    trials = list(completed_or_existing_trials)

    if max_trials is not None and len(trials) >= max_trials:
        raise SweepExhaustedError(f"Sweep reached max_trials={max_trials}")

    if method == "grid":
        keys = list(space.keys())
        value_lists = [_grid_values_for_param(space[k]) for k in keys]
        existing_params = [_extract_trial_params(t) for t in trials]
        for combo in itertools.product(*value_lists):
            candidate = dict(zip(keys, combo, strict=True))
            if not any(_params_match(candidate, ep, space) for ep in existing_params):
                return candidate
        raise SweepExhaustedError("Grid search space exhausted")

    rng = _make_rng(seed, len(trials))
    if method == "random":
        return _sample_random(space, rng)

    return _suggest_bayes(space, trials, metric_goal, rng, metric_name=metric_name)


def _extract_peer_value_at_step(peer: Any, target_step: int) -> float | None:
    best_step = -1
    best_val: float | None = None

    if isinstance(peer, Mapping):
        if target_step in peer and _is_number(peer[target_step]):
            val = float(peer[target_step])
            return val if math.isfinite(val) else None
        for step_raw, val_raw in peer.items():
            if _is_int(step_raw) and step_raw <= target_step and step_raw > best_step and _is_number(val_raw):
                val = float(val_raw)
                if math.isfinite(val):
                    best_step = step_raw
                    best_val = val
        return best_val

    if isinstance(peer, Iterable):
        for point in peer:
            if isinstance(point, Mapping):
                s = point.get("step")
                v = point.get("value")
            elif isinstance(point, (tuple, list)) and len(point) >= 2:
                s, v = point[0], point[1]
            else:
                continue
            if _is_int(s) and s <= target_step and s > best_step and _is_number(v):
                fv = float(v)
                if math.isfinite(fv):
                    best_step = s
                    best_val = fv
    return best_val


def evaluate_early_stopping(
    strategy: str | None,
    metric_goal: str,
    early_stopping_cfg: Mapping[str, Any] | None,
    current_step: int,
    current_value: float,
    peer_runs_metrics: Sequence[Any] | Mapping[str, Any],
) -> EarlyStopDecision:
    """Evaluate whether an active sweep trial should be stopped early."""
    strat = (strategy or "none").lower()
    if strat not in _VALID_STRATEGIES:
        raise ValueError(f"Unsupported early stopping strategy '{strategy}'")
    if metric_goal not in _VALID_GOALS:
        raise ValueError(f"Unsupported metric_goal '{metric_goal}'")

    if strat == "none":
        return EarlyStopDecision(False, None)

    if not _is_number(current_value) or not math.isfinite(float(current_value)):
        return EarlyStopDecision(True, "Non-finite metric value")
    cur_val = float(current_value)

    cfg = early_stopping_cfg or {}
    min_step = int(cfg.get("min_step", 10))
    if current_step < min_step:
        return EarlyStopDecision(False, None)

    peers_iter: Iterable[Any] = (
        peer_runs_metrics.values() if isinstance(peer_runs_metrics, Mapping) else peer_runs_metrics
    )

    if strat == "median":
        peer_vals = [
            v for p in peers_iter if (v := _extract_peer_value_at_step(p, current_step)) is not None
        ]
        if not peer_vals:
            return EarlyStopDecision(False, None)
        med = statistics.median(peer_vals)
        if metric_goal == "minimize" and cur_val > med:
            return EarlyStopDecision(
                True,
                f"Metric {cur_val:.6g} worse than peer median {med:.6g} at step {current_step}",
            )
        if metric_goal == "maximize" and cur_val < med:
            return EarlyStopDecision(
                True,
                f"Metric {cur_val:.6g} worse than peer median {med:.6g} at step {current_step}",
            )
        return EarlyStopDecision(False, None)

    # ASHA: exponential rungs min_step * (reduction_factor ** k)
    reduction_factor = max(2, int(cfg.get("reduction_factor", 3)))
    rung = min_step
    while rung * reduction_factor <= current_step:
        rung *= reduction_factor

    peer_vals = [v for p in peers_iter if (v := _extract_peer_value_at_step(p, rung)) is not None]
    if len(peer_vals) < reduction_factor:
        return EarlyStopDecision(False, None)

    all_vals = peer_vals + [cur_val]
    keep_count = max(1, len(all_vals) // reduction_factor)
    all_vals.sort(reverse=(metric_goal == "maximize"))
    cutoff = all_vals[keep_count - 1]

    if metric_goal == "minimize" and cur_val > cutoff:
        return EarlyStopDecision(
            True,
            f"ASHA rung {rung}: {cur_val:.6g} outside top 1/{reduction_factor} (cutoff {cutoff:.6g})",
        )
    if metric_goal == "maximize" and cur_val < cutoff:
        return EarlyStopDecision(
            True,
            f"ASHA rung {rung}: {cur_val:.6g} outside top 1/{reduction_factor} (cutoff {cutoff:.6g})",
        )
    return EarlyStopDecision(False, None)


def _rank_average_ties(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _pearson_abs(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    if den_x <= 0.0 or den_y <= 0.0:
        return 0.0
    corr = abs(num / math.sqrt(den_x * den_y))
    return min(1.0, corr) if math.isfinite(corr) else 0.0


def _spearman_abs(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2 or len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return 0.0
    return _pearson_abs(_rank_average_ties(xs), _rank_average_ties(ys))


def _anova_eta_squared(groups: Sequence[Any], ys: Sequence[float]) -> float:
    n = len(ys)
    if n < 2 or len(set(groups)) <= 1:
        return 0.0
    grand_mean = sum(ys) / n
    ss_total = sum((y - grand_mean) ** 2 for y in ys)
    if ss_total <= 0.0:
        return 0.0

    by_cat: dict[Any, list[float]] = {}
    for g, y in zip(groups, ys, strict=True):
        by_cat.setdefault(g, []).append(y)

    ss_between = sum(len(vals) * ((sum(vals) / len(vals)) - grand_mean) ** 2 for vals in by_cat.values())
    eta2 = ss_between / ss_total
    return min(1.0, max(0.0, eta2)) if math.isfinite(eta2) else 0.0


def build_sweep_analysis(sweep: Any, runs: Sequence[Any]) -> dict[str, Any]:
    """Compute hyperparameter importance and parallel-coordinates data for a sweep."""
    if isinstance(sweep, Mapping):
        metric_name = str(sweep.get("metric_name", "loss"))
        metric_goal = str(sweep.get("metric_goal", "minimize"))
        raw_space = sweep.get("search_space") or {}
    else:
        metric_name = str(getattr(sweep, "metric_name", "loss"))
        metric_goal = str(getattr(sweep, "metric_goal", "minimize"))
        raw_space = getattr(sweep, "search_space", None) or {}

    space = validate_search_space(raw_space) if raw_space else {}

    trials_out: list[dict[str, Any]] = []
    valid_pairs: list[tuple[dict[str, Any], float]] = []

    for run in runs:
        if isinstance(run, Mapping):
            run_id = run.get("id")
            run_name = run.get("name")
            status = run.get("status", "unknown")
        else:
            run_id = getattr(run, "id", None)
            run_name = getattr(run, "name", None)
            status = getattr(run, "status", "unknown")

        params = _extract_trial_params(run)
        metric_val = _extract_trial_metric(run, metric_name=metric_name)

        trials_out.append(
            {
                "run_id": run_id,
                "run_name": run_name,
                "status": status,
                "params": params,
                "metric_value": metric_val,
            }
        )
        if metric_val is not None:
            valid_pairs.append((params, metric_val))

    raw_importances: list[tuple[str, float, str]] = []
    for name, spec in space.items():
        if spec["type"] == "categorical":
            xs_cat: list[Any] = []
            ys_cat: list[float] = []
            for params, y in valid_pairs:
                if name in params:
                    xs_cat.append(params[name])
                    ys_cat.append(y)
            score = _anova_eta_squared(xs_cat, ys_cat)
            raw_importances.append((name, score, "anova_eta2"))
        else:
            xs_num: list[float] = []
            ys_num: list[float] = []
            for params, y in valid_pairs:
                val = params.get(name)
                if _is_number(val) and math.isfinite(float(val)):
                    xs_num.append(float(val))
                    ys_num.append(y)
            score = _spearman_abs(xs_num, ys_num)
            raw_importances.append((name, score, "spearman"))

    total_raw = sum(s for _, s, _ in raw_importances)
    if total_raw > 0.0:
        importance = [
            {"name": name, "importance": score / total_raw, "method": method}
            for name, score, method in raw_importances
        ]
    elif raw_importances:
        uniform = 1.0 / len(raw_importances)
        importance = [
            {"name": name, "importance": uniform, "method": method}
            for name, _, method in raw_importances
        ]
    else:
        importance = []

    importance.sort(key=lambda item: item["importance"], reverse=True)

    axes: list[dict[str, Any]] = []
    for name, spec in space.items():
        ax: dict[str, Any] = {"name": name, "type": spec["type"]}
        if spec["type"] == "categorical":
            ax["values"] = list(spec["values"])
        else:
            ax["min"] = spec["min"]
            ax["max"] = spec["max"]
            ax["log"] = spec["log"]
        axes.append(ax)

    metric_vals = [y for _, y in valid_pairs]
    metric_axis: dict[str, Any] = {
        "name": metric_name,
        "type": "float",
        "min": min(metric_vals) if metric_vals else 0.0,
        "max": max(metric_vals) if metric_vals else 1.0,
        "goal": metric_goal,
    }
    axes.append(metric_axis)

    return {
        "metric_name": metric_name,
        "metric_goal": metric_goal,
        "importance": importance,
        "parallel_coordinates": {
            "axes": axes,
            "trials": trials_out,
        },
    }
