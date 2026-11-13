"""Tests for pure-Python MLOps hyperparameter sweep engine and early stopping."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from matrix_server.mlops.sweeps import (
    EarlyStopDecision,
    InvalidSearchSpaceError,
    SweepExhaustedError,
    build_sweep_analysis,
    evaluate_early_stopping,
    suggest_next_params,
    validate_search_space,
)


def test_validate_search_space_explicit_and_shortcuts() -> None:
    space = {
        "optimizer": {"values": ["adamw", "sgd"]},
        "layers": {"min": 2, "max": 8, "step": 2},
        "lr": {"min": 1e-4, "max": 1e-1, "log": True},
        "dropout": {"type": "float", "min": 0.0, "max": 0.5},
        "batch_size": {"type": "int", "min": 16, "max": 64, "log": True},
    }
    normalized = validate_search_space(space)

    assert normalized["optimizer"]["type"] == "categorical"
    assert normalized["layers"]["type"] == "int"
    assert normalized["layers"]["step"] == 2
    assert normalized["lr"]["type"] == "float"
    assert normalized["lr"]["log"] is True
    assert normalized["dropout"]["type"] == "float"
    assert normalized["batch_size"]["type"] == "int"


@pytest.mark.parametrize(
    "bad_space",
    [
        {},
        {"x": {"type": "categorical", "values": []}},
        {"x": {"type": "int", "min": 10, "max": 5}},
        {"x": {"type": "int", "min": 0, "max": 10, "log": True}},
        {"x": {"type": "int", "min": 1, "max": 10, "step": 0}},
        {"x": {"type": "int", "min": True, "max": 10}},
        {"x": {"type": "float", "min": 0.0, "max": 1.0, "log": True}},
        {"x": {"type": "float", "min": float("nan"), "max": 1.0}},
        {"x": {"type": "float", "min": 0.0, "max": float("inf")}},
        {"x": {"type": "float", "min": 0.0, "max": 1.0, "values": [0.2, float("nan")]}},
        {"x": {"type": "float", "min": 0.0, "max": 1.0, "values": [0.2, float("inf")]}},
        {"x": {"type": "float", "min": 2.0, "max": 1.0}},
        {"x": {"type": "unknown", "values": [1, 2]}},
    ],
)
def test_validate_search_space_rejects_invalid(bad_space: dict) -> None:
    with pytest.raises(InvalidSearchSpaceError):
        validate_search_space(bad_space)


def test_grid_suggest_deterministic_and_exhaustion() -> None:
    space = {
        "opt": {"type": "categorical", "values": ["adam", "sgd"]},
        "epochs": {"type": "int", "min": 2, "max": 6, "step": 2},
    }
    trials: list[dict] = []
    seen: list[tuple[str, int]] = []

    for _ in range(6):
        params = suggest_next_params("grid", space, trials, "minimize", seed=123)
        seen.append((params["opt"], params["epochs"]))
        trials.append({"hyperparams": params, "metric_value": 0.5})

    assert seen == [
        ("adam", 2),
        ("adam", 4),
        ("adam", 6),
        ("sgd", 2),
        ("sgd", 4),
        ("sgd", 6),
    ]

    with pytest.raises(SweepExhaustedError):
        suggest_next_params("grid", space, trials, "minimize", seed=123)


def test_suggest_respects_max_trials() -> None:
    space = {"lr": {"min": 1e-4, "max": 1e-2, "log": True}}
    trials = [{"hyperparams": {"lr": 1e-3}, "metric_value": 0.2}]

    with pytest.raises(SweepExhaustedError):
        suggest_next_params("random", space, trials, "minimize", seed=1, max_trials=1)


def test_random_log_uniform_and_categorical_deterministic() -> None:
    space = {
        "lr": {"type": "float", "min": 1e-5, "max": 1e-1, "log": True},
        "rank": {"type": "int", "min": 4, "max": 64, "log": True},
        "scheduler": {"type": "categorical", "values": ["cosine", "linear", "constant"]},
    }

    first_a = suggest_next_params("random", space, [], "minimize", seed=42)
    first_b = suggest_next_params("random", space, [], "minimize", seed=42)
    assert first_a == first_b

    trials: list[dict] = []
    lrs: list[float] = []
    for _ in range(200):
        p = suggest_next_params("random", space, trials, "minimize", seed=42)
        assert 1e-5 <= p["lr"] <= 1e-1
        assert 4 <= p["rank"] <= 64 and isinstance(p["rank"], int)
        assert p["scheduler"] in {"cosine", "linear", "constant"}
        lrs.append(p["lr"])
        trials.append({"hyperparams": p})

    # Log-uniform on [1e-5, 1e-1] has geometric mean 1e-3 (far below linear midpoint ~0.05).
    mean_log10 = sum(math.log10(v) for v in lrs) / len(lrs)
    assert -3.4 < mean_log10 < -2.6


def test_bayes_tpe_converges_on_quadratic() -> None:
    space = {
        "x": {"type": "float", "min": 0.0, "max": 1.0},
        "y": {"type": "float", "min": 0.0, "max": 1.0},
    }

    def objective(p: dict[str, float]) -> float:
        return (p["x"] - 0.3) ** 2 + (p["y"] - 0.7) ** 2

    trials: list[dict] = []
    best_val = float("inf")

    for _ in range(30):
        params = suggest_next_params("bayes", space, trials, "minimize", seed=7)
        val = objective(params)
        best_val = min(best_val, val)
        trials.append({"hyperparams": params, "metric_value": val})

    assert best_val < 0.02


def test_bayes_tpe_with_summary_metrics_and_heartbeat() -> None:
    from matrix_server.mlops.sweeps import _extract_trial_metric

    space = {
        "x": {"type": "float", "min": 0.0, "max": 1.0},
        "y": {"type": "float", "min": 0.0, "max": 1.0},
    }

    def objective(p: dict[str, float]) -> float:
        return (p["x"] - 0.25) ** 2 + (p["y"] - 0.75) ** 2

    trials: list[SimpleNamespace] = []
    best_val = float("inf")
    for _ in range(30):
        params = suggest_next_params(
            "bayes",
            space,
            trials,
            "minimize",
            seed=11,
            metric_name="val_loss",
        )
        val = objective(params)
        best_val = min(best_val, val)
        trials.append(
            SimpleNamespace(
                hyperparams=params,
                summary_metrics={
                    "_last_heartbeat_at": "2026-11-13T10:00:00Z",
                    "val_loss": val,
                },
            )
        )

    assert _extract_trial_metric(trials[0]) is None
    assert _extract_trial_metric(trials[0], metric_name="val_loss") == pytest.approx(
        trials[0].summary_metrics["val_loss"]
    )
    assert best_val < 0.02


def test_early_stopping_none_and_median() -> None:
    assert evaluate_early_stopping("none", "minimize", None, 50, 999.0, [{50: 1.0}]) == EarlyStopDecision(
        False, None
    )

    peers = [{10: 0.4, 20: 0.3}, {10: 0.5, 20: 0.35}, {10: 0.6, 20: 0.4}]

    # Below min_step (default 10) -> never stops
    assert evaluate_early_stopping("median", "minimize", {"min_step": 10}, 5, 10.0, peers).should_stop is False

    # At step 20, median is 0.35 -> 0.55 stops, 0.20 continues
    bad = evaluate_early_stopping("median", "minimize", {"min_step": 10}, 20, 0.55, peers)
    good = evaluate_early_stopping("median", "minimize", {"min_step": 10}, 20, 0.20, peers)
    assert bad.should_stop is True
    assert bad.reason is not None
    assert good.should_stop is False

    # Maximize: median 0.35 -> 0.20 stops, 0.55 continues
    assert evaluate_early_stopping("median", "maximize", {"min_step": 10}, 20, 0.20, peers).should_stop is True
    assert evaluate_early_stopping("median", "maximize", {"min_step": 10}, 20, 0.55, peers).should_stop is False


def test_early_stopping_asha_rungs() -> None:
    cfg = {"min_step": 10, "reduction_factor": 3}
    # Rungs: 10, 30, 90...
    # At rung 10 with 3 peers + 1 current = 4 runs -> top 1/3 => keep top 1
    peers = [
        {10: 0.2, 30: 0.1},
        {10: 0.5, 30: 0.4},
        {10: 0.8, 30: 0.7},
    ]

    # Before min_step
    assert evaluate_early_stopping("asha", "minimize", cfg, 8, 5.0, peers).should_stop is False

    # Leader at step 10 (0.15 < 0.2) -> keep
    leader = evaluate_early_stopping("asha", "minimize", cfg, 10, 0.15, peers)
    assert leader.should_stop is False

    # Bad trial at step 10 (0.9 > 0.2) -> stop
    bad = evaluate_early_stopping("asha", "minimize", cfg, 10, 0.9, peers)
    assert bad.should_stop is True
    assert bad.reason is not None

    # Maximize: leader (0.95) kept, poor (0.1) stopped
    assert evaluate_early_stopping("asha", "maximize", cfg, 30, 0.95, peers).should_stop is False
    assert evaluate_early_stopping("asha", "maximize", cfg, 30, 0.1, peers).should_stop is True

    # Insufficient peers (< reduction_factor) -> do not stop
    assert evaluate_early_stopping("asha", "minimize", cfg, 10, 0.9, peers[:2]).should_stop is False


def test_build_sweep_analysis_importance_and_parallel_coords() -> None:
    sweep = SimpleNamespace(
        metric_name="val_loss",
        metric_goal="minimize",
        search_space={
            "lr": {"type": "float", "min": 1e-4, "max": 1e-1, "log": True},
            "opt": {"type": "categorical", "values": ["adamw", "sgd"]},
            "unused_const": {"type": "int", "min": 4, "max": 4},
        },
    )
    runs = [
        SimpleNamespace(
            id="r1",
            name="run-1",
            status="completed",
            hyperparams={"lr": 1e-4, "opt": "adamw", "unused_const": 4},
            summary_metrics={"val_loss": 0.10},
        ),
        SimpleNamespace(
            id="r2",
            name="run-2",
            status="completed",
            hyperparams={"lr": 1e-3, "opt": "adamw", "unused_const": 4},
            summary_metrics={"val_loss": 0.20},
        ),
        SimpleNamespace(
            id="r3",
            name="run-3",
            status="completed",
            hyperparams={"lr": 1e-2, "opt": "sgd", "unused_const": 4},
            summary_metrics={"val_loss": 0.50},
        ),
        SimpleNamespace(
            id="r4",
            name="run-4",
            status="completed",
            hyperparams={"lr": 1e-1, "opt": "sgd", "unused_const": 4},
            summary_metrics={"val_loss": 0.90},
        ),
    ]

    analysis = build_sweep_analysis(sweep, runs)
    importance = analysis["importance"]
    assert len(importance) == 3
    total_imp = sum(item["importance"] for item in importance)
    assert math.isclose(total_imp, 1.0, rel_tol=1e-6)
    for item in importance:
        assert math.isfinite(item["importance"])
        assert 0.0 <= item["importance"] <= 1.0

    # Constant parameter should have 0 importance
    by_name = {item["name"]: item["importance"] for item in importance}
    assert by_name["unused_const"] == 0.0
    assert by_name["lr"] > 0.0

    pc = analysis["parallel_coordinates"]
    assert len(pc["trials"]) == 4
    assert [ax["name"] for ax in pc["axes"]] == ["lr", "opt", "unused_const", "val_loss"]


def test_build_sweep_analysis_handles_constant_metrics_without_nan() -> None:
    sweep = {
        "metric_name": "loss",
        "metric_goal": "minimize",
        "search_space": {"lr": {"min": 0.001, "max": 0.1}},
    }
    runs = [
        {"id": "r1", "name": "r1", "status": "completed", "hyperparams": {"lr": 0.01}, "summary_metrics": {"loss": 0.5}},
        {"id": "r2", "name": "r2", "status": "completed", "hyperparams": {"lr": 0.05}, "summary_metrics": {"loss": 0.5}},
    ]
    analysis = build_sweep_analysis(sweep, runs)
    imp = analysis["importance"]
    assert len(imp) == 1
    assert math.isfinite(imp[0]["importance"])
    assert math.isclose(imp[0]["importance"], 1.0)
