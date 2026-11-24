"""Hyperparameter sweep engine: validation, grid/random/TPE sampling, ASHA/Hyperband pruning, RF importance."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_GRID_COMBINATIONS = 100_000
MAX_SWEEP_RUNS = 10_000
MIN_IMPORTANCE_RUNS = 10
DEFAULT_TPE_STARTUP = 10
DEFAULT_TPE_GAMMA = 0.25
DEFAULT_TPE_CANDIDATES = 24
DEFAULT_ETA = 3

VALID_METHODS = frozenset({"grid", "random", "tpe"})
METHOD_ALIASES = {"bayes": "tpe"}
VALID_GOALS = frozenset({"minimize", "maximize"})
VALID_EARLY_STOP_TYPES = frozenset({"asha", "hyperband"})
COMPAT_EARLY_STOP_TYPES = frozenset({"asha", "hyperband", "median"})


class SweepValidationError(ValueError):
    """Raised when a sweep config or search space is invalid, carrying per-field messages."""

    def __init__(self, errors: dict[str, str], message: str | None = None) -> None:
        self.errors = dict(errors)
        if message is None:
            first_key, first_msg = next(iter(self.errors.items()), ("config", "invalid sweep config"))
            message = f"{first_key}: {first_msg}"
        super().__init__(message)


class SweepExhaustedError(Exception):
    """Raised when a sweep has no remaining configurations to suggest."""


@dataclass(frozen=True, slots=True)
class PruneDecision:
    should_stop: bool
    reason: str | None = None
    rung: int | None = None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_json_value(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_finite_json_value(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_is_finite_json_value(v) for v in value)
    return value is None or isinstance(value, (str, int, bool))


def _normalize_param(name: str, raw_spec: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw_spec, Mapping):
        return None, "parameter spec must be an object"

    ptype = raw_spec.get("type")
    if ptype == "categorical":
        ptype = "choice"
    if ptype is None:
        if "value" in raw_spec and "values" not in raw_spec and "min" not in raw_spec:
            ptype = "constant"
        elif "values" in raw_spec:
            ptype = "choice"
        elif "min" in raw_spec and "max" in raw_spec:
            step_val = raw_spec.get("step")
            if (
                _is_int(raw_spec["min"])
                and _is_int(raw_spec["max"])
                and (step_val is None or _is_int(step_val))
                and "q" not in raw_spec
            ):
                ptype = "int"
            else:
                ptype = "float"
        else:
            return None, "must define 'type', 'values', 'value', or ('min', 'max')"

    if ptype == "choice":
        values = raw_spec.get("values")
        if not isinstance(values, (list, tuple)) or len(values) == 0:
            return None, "choice parameter requires a non-empty 'values' list"
        for v in values:
            if not (v is None or isinstance(v, (str, int, float, bool))) or (
                isinstance(v, float) and not math.isfinite(v)
            ):
                return None, "choice values must be finite strings, numbers, booleans, or null"
        return {"type": "choice", "values": list(values)}, None

    if ptype == "constant":
        if "value" not in raw_spec:
            return None, "constant parameter requires 'value'"
        val = raw_spec["value"]
        if not _is_finite_json_value(val):
            return None, "constant value must be finite JSON"
        return {"type": "constant", "value": val}, None

    if ptype == "int":
        min_v = raw_spec.get("min")
        max_v = raw_spec.get("max")
        step_v = raw_spec.get("step", 1)
        log_v = bool(raw_spec.get("log", False))
        if not _is_int(min_v) or not _is_int(max_v):
            return None, "int parameter requires integer 'min' and 'max'"
        if not _is_int(step_v) or step_v < 1:
            return None, "int parameter 'step' must be an integer >= 1"
        if min_v > max_v:
            return None, "int parameter requires min <= max"
        if log_v and min_v <= 0:
            return None, "int parameter with log=true requires min > 0"
        return {
            "type": "int",
            "min": int(min_v),
            "max": int(max_v),
            "step": int(step_v),
            "log": log_v,
        }, None

    if ptype == "float":
        min_v = raw_spec.get("min")
        max_v = raw_spec.get("max")
        log_v = bool(raw_spec.get("log", False))
        q_v = raw_spec.get("q")
        if not _is_number(min_v) or not _is_number(max_v):
            return None, "float parameter requires numeric 'min' and 'max'"
        fmin, fmax = float(min_v), float(max_v)
        if not (math.isfinite(fmin) and math.isfinite(fmax)):
            return None, "float parameter bounds must be finite"
        if fmin > fmax:
            return None, "float parameter requires min <= max"
        if log_v and fmin <= 0.0:
            return None, "float parameter with log=true requires min > 0"
        spec: dict[str, Any] = {"type": "float", "min": fmin, "max": fmax, "log": log_v}
        if q_v is not None:
            if not _is_number(q_v) or not math.isfinite(float(q_v)) or float(q_v) <= 0.0:
                return None, "float parameter 'q' must be a finite number > 0"
            spec["q"] = float(q_v)
        if "values" in raw_spec and isinstance(raw_spec["values"], (list, tuple)):
            vals: list[float] = []
            for v in raw_spec["values"]:
                if not _is_number(v) or not math.isfinite(float(v)):
                    return None, "float parameter 'values' must be finite numbers"
                vals.append(float(v))
            spec["values"] = vals
        return spec, None

    return None, f"unsupported parameter type '{ptype}'"


def grid_values_for_param(spec: Mapping[str, Any]) -> list[Any]:
    ptype = spec["type"]
    if ptype == "choice":
        return list(spec["values"])
    if ptype == "constant":
        return [spec["value"]]
    if ptype == "int":
        return list(range(int(spec["min"]), int(spec["max"]) + 1, int(spec.get("step", 1))))
    raise ValueError(f"parameter type '{ptype}' is not discrete for grid search")


def grid_size(parameters: Mapping[str, Mapping[str, Any]]) -> int:
    total = 1
    for spec in parameters.values():
        ptype = spec["type"]
        if ptype == "choice":
            count = len(spec["values"])
        elif ptype == "constant":
            count = 1
        elif ptype == "int":
            count = ((int(spec["max"]) - int(spec["min"])) // int(spec.get("step", 1))) + 1
        else:
            raise ValueError(f"parameter type '{ptype}' is not allowed in grid search")
        total *= count
    return total


def validate_sweep_config(
    raw_config: Mapping[str, Any],
    *,
    allow_median: bool = False,
    allow_float_grid: bool = False,
) -> dict[str, Any]:
    """Validate and normalize a G2 SweepConfig. Raises SweepValidationError with per-parameter messages."""
    if not isinstance(raw_config, Mapping):
        raise SweepValidationError({"config": "config must be an object"})

    errors: dict[str, str] = {}

    raw_method = raw_config.get("method")
    if not isinstance(raw_method, str):
        errors["method"] = "method is required ('grid', 'random', or 'tpe')"
        method = "random"
    else:
        method = METHOD_ALIASES.get(raw_method.lower(), raw_method.lower())
        if method not in VALID_METHODS:
            errors["method"] = f"unsupported method '{raw_method}'; expected 'grid', 'random', or 'tpe'"

    raw_metric = raw_config.get("metric")
    metric_key = ""
    metric_goal = "minimize"
    if not isinstance(raw_metric, Mapping):
        errors["metric"] = "metric must be an object with 'key' and 'goal'"
    else:
        rk = raw_metric.get("key")
        rg = raw_metric.get("goal", "minimize")
        if not isinstance(rk, str) or not rk.strip():
            errors["metric.key"] = "metric.key must be a non-empty string"
        elif len(rk.strip()) > 200:
            errors["metric.key"] = "metric.key must be at most 200 characters"
        else:
            metric_key = rk.strip()
        if not isinstance(rg, str) or rg not in VALID_GOALS:
            errors["metric.goal"] = "metric.goal must be 'minimize' or 'maximize'"
        else:
            metric_goal = rg

    raw_params = raw_config.get("parameters")
    normalized_params: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_params, Mapping) or len(raw_params) == 0:
        errors["parameters"] = "parameters must be a non-empty mapping"
    else:
        for pname, pspec in raw_params.items():
            if not isinstance(pname, str) or not pname.strip():
                errors["parameters"] = "parameter names must be non-empty strings"
                continue
            spec, err = _normalize_param(pname, pspec)
            if err is not None:
                errors[f"parameters.{pname}"] = err
            elif spec is not None:
                normalized_params[pname] = spec

    if method == "grid" and normalized_params:
        total = 1
        grid_ok = True
        for pname, spec in normalized_params.items():
            if spec["type"] == "float":
                if allow_float_grid:
                    n_vals = len(spec["values"]) if spec.get("values") else 5
                    total *= n_vals
                else:
                    errors[f"parameters.{pname}"] = (
                        "grid method only supports 'choice', 'int' (with step), and 'constant' parameters"
                    )
                    grid_ok = False
            elif spec["type"] == "choice":
                total *= len(spec["values"])
            elif spec["type"] == "constant":
                total *= 1
            elif spec["type"] == "int":
                total *= ((spec["max"] - spec["min"]) // spec["step"]) + 1
        if grid_ok and total > MAX_GRID_COMBINATIONS:
            errors["parameters"] = (
                f"grid search space has {total} combinations, exceeding limit of {MAX_GRID_COMBINATIONS}"
            )

    max_runs = raw_config.get("max_runs")
    if max_runs is not None and (not _is_int(max_runs) or max_runs < 1 or max_runs > MAX_SWEEP_RUNS):
        errors["max_runs"] = f"max_runs must be an integer between 1 and {MAX_SWEEP_RUNS}"

    seed = raw_config.get("seed")
    if seed is not None and not _is_int(seed):
        errors["seed"] = "seed must be an integer"

    n_startup = raw_config.get("n_startup_trials")
    if n_startup is not None and (not _is_int(n_startup) or n_startup < 1 or n_startup > 1000):
        errors["n_startup_trials"] = "n_startup_trials must be an integer between 1 and 1000"

    early_stop_out: dict[str, Any] | None = None
    raw_es = raw_config.get("early_stop")
    if raw_es is not None:
        if not isinstance(raw_es, Mapping):
            errors["early_stop"] = "early_stop must be an object"
        else:
            es_type = raw_es.get("type")
            allowed = COMPAT_EARLY_STOP_TYPES if allow_median else VALID_EARLY_STOP_TYPES
            if not isinstance(es_type, str) or es_type.lower() not in allowed:
                errors["early_stop.type"] = (
                    f"early_stop.type must be one of {sorted(allowed)}"
                )
            min_iter = raw_es.get("min_iter")
            if not _is_int(min_iter) or min_iter < 1:
                errors["early_stop.min_iter"] = "early_stop.min_iter must be an integer >= 1"
            max_iter = raw_es.get("max_iter")
            if max_iter is not None and (
                not _is_int(max_iter) or (_is_int(min_iter) and max_iter < min_iter) or max_iter < 1
            ):
                errors["early_stop.max_iter"] = "early_stop.max_iter must be an integer >= min_iter"
            eta = raw_es.get("eta", DEFAULT_ETA)
            if not _is_int(eta) or eta < 2:
                errors["early_stop.eta"] = "early_stop.eta must be an integer >= 2"
            if not any(k.startswith("early_stop") for k in errors):
                early_stop_out = {
                    "type": str(es_type).lower(),
                    "min_iter": int(min_iter),
                    "eta": int(eta),
                }
                if max_iter is not None:
                    early_stop_out["max_iter"] = int(max_iter)

    if errors:
        raise SweepValidationError(errors)

    out: dict[str, Any] = {
        "method": method,
        "metric": {"key": metric_key, "goal": metric_goal},
        "parameters": normalized_params,
    }
    if early_stop_out is not None:
        out["early_stop"] = early_stop_out
    if max_runs is not None:
        out["max_runs"] = int(max_runs)
    if seed is not None:
        out["seed"] = int(seed)
    if n_startup is not None:
        out["n_startup_trials"] = int(n_startup)
    return out


# --------------------------------------------------------------------------- sampling


def _make_rng(seed: int | str | None, trial_index: int) -> random.Random:
    if seed is None:
        return random.Random()
    return random.Random(f"matrix-sweep:{seed}:{trial_index}")


def _snap_int(raw: float, spec: Mapping[str, Any]) -> int:
    min_v, max_v, step = int(spec["min"]), int(spec["max"]), int(spec.get("step", 1))
    if min_v == max_v:
        return min_v
    snapped = min_v + round((raw - min_v) / step) * step
    max_grid = min_v + ((max_v - min_v) // step) * step
    return max(min_v, min(max_grid, int(snapped)))


def _snap_float(raw: float, spec: Mapping[str, Any]) -> float:
    fmin, fmax = float(spec["min"]), float(spec["max"])
    val = max(fmin, min(fmax, float(raw)))
    q = spec.get("q")
    if q is not None:
        qf = float(q)
        snapped = round(val / qf) * qf
        if snapped < fmin:
            snapped = snapped + qf if snapped + qf <= fmax + 1e-12 else fmin
        elif snapped > fmax:
            snapped = snapped - qf if snapped - qf >= fmin - 1e-12 else fmax
        # Clean floating point representation artifacts from q multiplication
        val = max(fmin, min(fmax, round(snapped, 12)))
    return val


def _sample_param(spec: Mapping[str, Any], rng: random.Random) -> Any:
    ptype = spec["type"]
    if ptype == "constant":
        return spec["value"]
    if ptype == "choice":
        return rng.choice(spec["values"])
    if ptype == "int":
        min_v, max_v, step = int(spec["min"]), int(spec["max"]), int(spec.get("step", 1))
        if min_v == max_v:
            return min_v
        if spec.get("log"):
            raw = math.exp(rng.uniform(math.log(min_v), math.log(max_v)))
            return _snap_int(raw, spec)
        count = (max_v - min_v) // step
        return min_v + rng.randint(0, count) * step
    # float
    fmin, fmax = float(spec["min"]), float(spec["max"])
    if math.isclose(fmin, fmax):
        return fmin
    if spec.get("log"):
        raw = math.exp(rng.uniform(math.log(fmin), math.log(fmax)))
    else:
        raw = rng.uniform(fmin, fmax)
    return _snap_float(raw, spec)


def _sample_random(parameters: Mapping[str, Mapping[str, Any]], rng: random.Random) -> dict[str, Any]:
    return {name: _sample_param(spec, rng) for name, spec in parameters.items()}


def _grid_values_compat(spec: Mapping[str, Any]) -> list[Any]:
    if spec["type"] == "float":
        if spec.get("values"):
            return list(spec["values"])
        fmin, fmax = float(spec["min"]), float(spec["max"])
        if math.isclose(fmin, fmax):
            return [fmin]
        n_points = 5
        if spec.get("log"):
            lo, hi = math.log(fmin), math.log(fmax)
            return [math.exp(lo + (hi - lo) * i / (n_points - 1)) for i in range(n_points)]
        return [fmin + (fmax - fmin) * i / (n_points - 1) for i in range(n_points)]
    return grid_values_for_param(spec)


def _grid_param_size(spec: Mapping[str, Any]) -> int:
    ptype = spec["type"]
    if ptype == "choice":
        return len(spec["values"])
    if ptype == "constant":
        return 1
    if ptype == "int":
        return ((int(spec["max"]) - int(spec["min"])) // int(spec.get("step", 1))) + 1
    if ptype == "float":
        if spec.get("values"):
            return len(spec["values"])
        fmin, fmax = float(spec["min"]), float(spec["max"])
        return 1 if math.isclose(fmin, fmax) else 5
    raise ValueError(f"parameter type '{ptype}' is not allowed in grid search")


def _grid_param_at(spec: Mapping[str, Any], pos: int) -> Any:
    ptype = spec["type"]
    if ptype == "choice":
        return spec["values"][pos]
    if ptype == "constant":
        return spec["value"]
    if ptype == "int":
        return int(spec["min"]) + pos * int(spec.get("step", 1))
    return _grid_values_compat(spec)[pos]


def _feistel_permute(index: int, total: int, seed: int | str) -> int:
    """O(1)-time, O(1)-memory deterministic bijection on [0, total) via cycle-walking Feistel."""
    if total <= 1:
        return 0
    digest = hashlib.sha256(f"matrix-grid:{seed}:{total}".encode()).digest()
    keys = (
        int.from_bytes(digest[0:8], "big"),
        int.from_bytes(digest[8:16], "big"),
        int.from_bytes(digest[16:24], "big"),
        int.from_bytes(digest[24:32], "big"),
    )
    half = max(3, ((total - 1).bit_length() + 1) // 2)
    mask = (1 << half) - 1

    def _round_mix(val: int, key: int) -> int:
        x = (val + key + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 31
        return x & mask

    x = index
    while True:
        left = (x >> half) & mask
        right = x & mask
        for k in keys:
            left, right = right, left ^ _round_mix(right, k)
        x = (left << half) | right
        if x < total:
            return x


def _suggest_grid(
    parameters: Mapping[str, Mapping[str, Any]],
    trial_index: int,
    seed: int | str | None,
) -> dict[str, Any]:
    keys = list(parameters.keys())
    specs = [parameters[k] for k in keys]
    sizes = [_grid_param_size(s) for s in specs]
    total = 1
    for sz in sizes:
        total *= sz
    if trial_index < 0 or trial_index >= total:
        raise SweepExhaustedError("grid search space exhausted")

    flat_idx = _feistel_permute(trial_index, total, seed) if seed is not None else trial_index

    result: dict[str, Any] = {}
    rem = flat_idx
    for k, spec, sz in zip(reversed(keys), reversed(specs), reversed(sizes), strict=True):
        rem, pos = divmod(rem, sz)
        result[k] = _grid_param_at(spec, pos)
    return {k: result[k] for k in keys}


# --------------------------------------------------------------------------- univariate TPE


def _to_working_coord(value: Any, spec: Mapping[str, Any]) -> float:
    fval = float(value)
    if spec.get("log"):
        return math.log(max(fval, 1e-300))
    return fval


def _from_working_coord(z: float, spec: Mapping[str, Any]) -> Any:
    raw = math.exp(z) if spec.get("log") else z
    if spec["type"] == "int":
        return _snap_int(raw, spec)
    return _snap_float(raw, spec)


def _parzen_components(z_obs: Sequence[float], z_min: float, z_max: float) -> tuple[list[float], list[float]]:
    """TPE Gaussian Parzen components: observations with neighbor-distance bandwidths, magic-clip floor, + prior."""
    span = max(z_max - z_min, 1e-9)
    mid = 0.5 * (z_min + z_max)
    if not z_obs:
        return [mid], [span]

    pts = sorted(z_obs)
    n = len(pts)
    min_bw = max(span / min(100.0, 1.0 + n), 1e-6)
    max_bw = span

    aug = sorted([*pts, mid])
    means: list[float] = []
    sigmas: list[float] = []
    for z in pts:
        idx = bisect_left(aug, z)
        left = z - aug[idx - 1] if idx > 0 else z - z_min
        right = aug[idx + 1] - z if idx + 1 < len(aug) else z_max - z
        bw = max(min_bw, min(max_bw, max(left, right)))
        means.append(z)
        sigmas.append(bw)

    # Prior component (weight 1 alongside n observations) prevents collapse onto early lucky points.
    means.append(mid)
    sigmas.append(span)
    return means, sigmas


def _parzen_log_density_numeric(
    candidate_val: Any,
    group_vals: Sequence[Any],
    spec: Mapping[str, Any],
) -> float:
    z_min = _to_working_coord(spec["min"], spec)
    z_max = _to_working_coord(spec["max"], spec)
    z_cand = _to_working_coord(candidate_val, spec)
    z_obs = [_to_working_coord(v, spec) for v in group_vals]

    means, sigmas = _parzen_components(z_obs, z_min, z_max)
    m = len(means)
    inv_sqrt_2pi = 1.0 / math.sqrt(2.0 * math.pi)
    kernel_sum = 0.0
    for mu, bw in zip(means, sigmas, strict=True):
        diff = (z_cand - mu) / bw
        kernel_sum += math.exp(-0.5 * diff * diff) * (inv_sqrt_2pi / bw)

    density = kernel_sum / m
    return math.log(max(density, 1e-300))


def _sample_from_good_numeric(
    good_vals: Sequence[Any],
    spec: Mapping[str, Any],
    rng: random.Random,
) -> Any:
    z_min = _to_working_coord(spec["min"], spec)
    z_max = _to_working_coord(spec["max"], spec)
    z_obs = [_to_working_coord(v, spec) for v in good_vals]
    if not z_obs:
        return _sample_param(spec, rng)
    means, sigmas = _parzen_components(z_obs, z_min, z_max)
    idx = rng.randrange(len(means))
    mu, bw = means[idx], sigmas[idx]
    for _ in range(16):
        z = rng.gauss(mu, bw)
        if z_min <= z <= z_max:
            return _from_working_coord(z, spec)
    return _sample_param(spec, rng)


def _categorical_log_prob(candidate_val: Any, group_vals: Sequence[Any], choices: Sequence[Any]) -> float:
    alpha = 1.0
    count = sum(1 for v in group_vals if v == candidate_val)
    prob = (count + alpha) / (len(group_vals) + alpha * max(len(choices), 1))
    return math.log(max(prob, 1e-300))


def _sample_from_good_categorical(
    good_vals: Sequence[Any],
    choices: Sequence[Any],
    rng: random.Random,
) -> Any:
    alpha = 1.0
    weights = [sum(1 for v in good_vals if v == c) + alpha for c in choices]
    return rng.choices(list(choices), weights=weights, k=1)[0]


def _suggest_tpe(
    parameters: Mapping[str, Mapping[str, Any]],
    scored_trials: Sequence[tuple[dict[str, Any], float]],
    metric_goal: str,
    rng: random.Random,
    *,
    gamma: float = DEFAULT_TPE_GAMMA,
    n_candidates: int = DEFAULT_TPE_CANDIDATES,
    n_startup_trials: int = DEFAULT_TPE_STARTUP,
) -> dict[str, Any]:
    finite_scored = [
        (params, float(val))
        for params, val in scored_trials
        if _is_number(val) and math.isfinite(float(val))
    ]
    if len(finite_scored) < n_startup_trials:
        return _sample_random(parameters, rng)

    reverse = metric_goal == "maximize"
    finite_scored.sort(key=lambda item: item[1], reverse=reverse)

    n_good = max(1, min(len(finite_scored) - 1, math.ceil(len(finite_scored) * gamma)))
    good_params = [p for p, _ in finite_scored[:n_good]]
    bad_params = [p for p, _ in finite_scored[n_good:]]

    good_by_param: dict[str, list[Any]] = {}
    bad_by_param: dict[str, list[Any]] = {}
    for name in parameters:
        good_by_param[name] = [p[name] for p in good_params if name in p]
        bad_by_param[name] = [p[name] for p in bad_params if name in p]

    best_candidate: dict[str, Any] | None = None
    best_score = -float("inf")

    for _ in range(n_candidates):
        cand: dict[str, Any] = {}
        log_ratio = 0.0
        for name, spec in parameters.items():
            ptype = spec["type"]
            if ptype == "constant":
                cand[name] = spec["value"]
                continue
            g_vals = good_by_param[name]
            b_vals = bad_by_param[name]
            if ptype == "choice":
                c_val = _sample_from_good_categorical(g_vals, spec["values"], rng)
                l_log = _categorical_log_prob(c_val, g_vals, spec["values"])
                g_log = _categorical_log_prob(c_val, b_vals, spec["values"])
            else:
                c_val = _sample_from_good_numeric(g_vals, spec, rng)
                l_log = _parzen_log_density_numeric(c_val, g_vals, spec)
                g_log = _parzen_log_density_numeric(c_val, b_vals, spec)
            cand[name] = c_val
            log_ratio += l_log - g_log

        if log_ratio > best_score:
            best_score = log_ratio
            best_candidate = cand

    return best_candidate if best_candidate is not None else _sample_random(parameters, rng)


def suggest_next(
    config: Mapping[str, Any],
    trial_index: int,
    scored_trials: Sequence[tuple[dict[str, Any], float]] = (),
) -> dict[str, Any]:
    """Suggest the next hyperparameter configuration for a validated SweepConfig."""
    max_runs = config.get("max_runs")
    if max_runs is not None and trial_index >= int(max_runs):
        raise SweepExhaustedError(f"sweep reached max_runs={max_runs}")

    method = METHOD_ALIASES.get(str(config["method"]).lower(), str(config["method"]).lower())
    parameters = config["parameters"]
    seed = config.get("seed")
    metric_goal = config["metric"]["goal"]

    if method == "grid":
        return _suggest_grid(parameters, trial_index, seed)

    rng = _make_rng(seed, trial_index)
    if method == "random":
        return _sample_random(parameters, rng)

    n_startup = int(config.get("n_startup_trials", DEFAULT_TPE_STARTUP))
    return _suggest_tpe(
        parameters,
        scored_trials,
        metric_goal,
        rng,
        gamma=DEFAULT_TPE_GAMMA,
        n_candidates=DEFAULT_TPE_CANDIDATES,
        n_startup_trials=n_startup,
    )


# --------------------------------------------------------------------------- ASHA / Hyperband pruning


def num_brackets(early_stop: Mapping[str, Any] | None) -> int:
    if not early_stop:
        return 1
    es_type = str(early_stop.get("type", "")).lower()
    if es_type != "hyperband":
        return 1
    min_iter = int(early_stop["min_iter"])
    max_iter = early_stop.get("max_iter")
    eta = max(2, int(early_stop.get("eta", DEFAULT_ETA)))
    if max_iter is None or int(max_iter) < min_iter * eta:
        return 1
    s_max = math.floor(math.log(int(max_iter) / min_iter, eta))
    return max(1, s_max + 1)


def assign_bracket(early_stop: Mapping[str, Any] | None, trial_index: int) -> int:
    return trial_index % num_brackets(early_stop)


def _peer_value_at_step(peer: Any, target_step: int, *, require_reached: bool = False) -> float | None:
    best_step = -1
    best_val: float | None = None
    max_step = -1

    if isinstance(peer, Mapping):
        for s_raw, v_raw in peer.items():
            if _is_int(s_raw) and _is_number(v_raw):
                fv = float(v_raw)
                if math.isfinite(fv):
                    max_step = max(max_step, s_raw)
                    if s_raw <= target_step and s_raw > best_step:
                        best_step = s_raw
                        best_val = fv
        if require_reached and max_step < target_step:
            return None
        return best_val

    if isinstance(peer, Iterable):
        for point in peer:
            if isinstance(point, Mapping):
                s, v = point.get("step"), point.get("value")
            elif isinstance(point, (tuple, list)) and len(point) >= 2:
                s, v = point[0], point[1]
            else:
                continue
            if _is_int(s) and _is_number(v):
                fv = float(v)
                if math.isfinite(fv):
                    max_step = max(max_step, s)
                    if s <= target_step and s > best_step:
                        best_step = s
                        best_val = fv
        if require_reached and max_step < target_step:
            return None
        return best_val

    return None


def pruning_target_step(
    early_stop: Mapping[str, Any] | None,
    step: int,
    value: float,
    *,
    bracket: int = 0,
) -> tuple[int | None, bool, PruneDecision | None]:
    """Determine the active rung/target step for pruning, or return an immediate decision."""
    if not early_stop:
        return None, False, PruneDecision(False)

    es_type = str(early_stop.get("type", "")).lower()
    if es_type not in COMPAT_EARLY_STOP_TYPES:
        return None, False, PruneDecision(False)

    if not _is_number(value) or not math.isfinite(float(value)):
        return None, False, PruneDecision(True, "non-finite metric value")

    min_iter = int(early_stop["min_iter"])
    max_iter = early_stop.get("max_iter")
    eta = max(2, int(early_stop.get("eta", DEFAULT_ETA)))

    if es_type == "median":
        if step < min_iter:
            return None, False, PruneDecision(False)
        return step, False, None

    # ASHA / Hyperband: bracket starts at r0 = min_iter * eta^bracket
    r0 = min_iter * (eta ** max(0, bracket))
    if step < r0:
        return None, False, PruneDecision(False)
    if max_iter is not None and step > int(max_iter):
        return None, False, PruneDecision(False)

    # Highest rung r = r0 * eta^k <= step (and <= max_iter if bounded)
    rung = r0
    while rung * eta <= step and (max_iter is None or rung * eta <= int(max_iter)):
        rung *= eta
    return rung, True, None


def evaluate_pruning(
    early_stop: Mapping[str, Any] | None,
    metric_goal: str,
    step: int,
    value: float,
    peer_series: Sequence[Any],
    *,
    bracket: int = 0,
) -> PruneDecision:
    """Evaluate asynchronous ASHA/Hyperband (or legacy median) pruning at (step, value)."""
    target_step, require_reached, immediate = pruning_target_step(
        early_stop, step, value, bracket=bracket
    )
    if immediate is not None:
        return immediate
    assert early_stop is not None and target_step is not None

    cur_val = float(value)
    es_type = str(early_stop.get("type", "")).lower()
    eta = max(2, int(early_stop.get("eta", DEFAULT_ETA)))

    if es_type == "median":
        peer_vals = [v for p in peer_series if (v := _peer_value_at_step(p, target_step)) is not None]
        if not peer_vals:
            return PruneDecision(False)
        med = statistics.median(peer_vals)
        if (metric_goal == "minimize" and cur_val > med) or (metric_goal == "maximize" and cur_val < med):
            return PruneDecision(
                True, f"metric {cur_val:.6g} worse than peer median {med:.6g} at step {step}", step
            )
        return PruneDecision(False)

    peer_vals = [
        v
        for p in peer_series
        if (v := _peer_value_at_step(p, target_step, require_reached=require_reached)) is not None
    ]
    all_vals = peer_vals + [cur_val]
    if len(all_vals) < eta:
        return PruneDecision(False, None, target_step)

    keep_count = max(1, len(all_vals) // eta)
    all_vals.sort(reverse=(metric_goal == "maximize"))
    cutoff = all_vals[keep_count - 1]

    if metric_goal == "minimize" and cur_val > cutoff:
        return PruneDecision(
            True,
            f"{es_type} rung {target_step}: {cur_val:.6g} outside top 1/{eta} (cutoff {cutoff:.6g})",
            target_step,
        )
    if metric_goal == "maximize" and cur_val < cutoff:
        return PruneDecision(
            True,
            f"{es_type} rung {target_step}: {cur_val:.6g} outside top 1/{eta} (cutoff {cutoff:.6g})",
            target_step,
        )
    return PruneDecision(False, None, target_step)


# --------------------------------------------------------------------------- RF importance


def compute_importance(
    parameters: Mapping[str, Mapping[str, Any]],
    completed_trials: Sequence[tuple[dict[str, Any], float]],
    *,
    seed: int | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Compute Random Forest hyperparameter importance (requires >= 10 completed runs & scikit-learn).

    Returns (importance_list_or_None, note_or_None).
    """
    finite = [
        (params, float(val))
        for params, val in completed_trials
        if _is_number(val) and math.isfinite(float(val))
    ]
    if len(finite) < MIN_IMPORTANCE_RUNS:
        return None, f"requires at least {MIN_IMPORTANCE_RUNS} completed runs"

    try:
        from sklearn.ensemble import RandomForestRegressor
    except ImportError:
        return None, "scikit-learn is not installed (install the analytics extra to compute importance)"

    param_names = list(parameters.keys())
    col_owner: list[str] = []
    # Build feature specification: numeric -> 1 column (log-transformed if log=True), choice -> one-hot
    choice_maps: dict[str, list[Any]] = {}
    for name in param_names:
        spec = parameters[name]
        ptype = spec["type"]
        if ptype == "constant":
            continue
        if ptype == "choice":
            vals = list(spec["values"])
            choice_maps[name] = vals
            for _ in vals:
                col_owner.append(name)
        else:
            col_owner.append(name)

    if not col_owner:
        return [{"param": name, "importance": 0.0} for name in param_names], None

    X: list[list[float]] = []
    y: list[float] = []
    for params, target in finite:
        row: list[float] = []
        ok = True
        for name in param_names:
            spec = parameters[name]
            ptype = spec["type"]
            if ptype == "constant":
                continue
            val = params.get(name)
            if ptype == "choice":
                for choice_val in choice_maps[name]:
                    row.append(1.0 if val == choice_val else 0.0)
            else:
                if not _is_number(val) or not math.isfinite(float(val)):
                    ok = False
                    break
                row.append(_to_working_coord(val, spec))
        if ok:
            X.append(row)
            y.append(target)

    if len(X) < MIN_IMPORTANCE_RUNS:
        return None, f"requires at least {MIN_IMPORTANCE_RUNS} completed runs with valid parameters"

    rf = RandomForestRegressor(n_estimators=64, random_state=seed if seed is not None else 42)
    rf.fit(X, y)

    raw_scores: dict[str, float] = {name: 0.0 for name in param_names}
    for owner, imp in zip(col_owner, rf.feature_importances_, strict=True):
        if math.isfinite(float(imp)):
            raw_scores[owner] += max(0.0, float(imp))

    total = sum(raw_scores.values())
    if total > 0.0:
        result = [{"param": name, "importance": raw_scores[name] / total} for name in param_names]
    else:
        uniform = 1.0 / len(param_names) if param_names else 0.0
        result = [{"param": name, "importance": uniform} for name in param_names]

    result.sort(key=lambda item: item["importance"], reverse=True)
    return result, None
