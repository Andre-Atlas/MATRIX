"""Typed report validation (contract G1, "Relatórios tipados"): valid and invalid cases per kind."""

import copy

import pytest

from matrix_server.runs.reports import ReportError, validate_report

CLASSIFICATION = {
    "labels": ["a", "b"],
    "metrics": {"accuracy": 0.5, "log_loss": 0.7},
    "per_class": [{"label": "a", "precision": 0.5, "recall": 0.5, "f1": 0.5, "support": 3}],
    "confusion_matrix": [[1, 2], [3, 4]],
    "roc": [{"label": "a", "fpr": [0, 1], "tpr": [0, 1], "auc": 0.5}],
    "pr": [{"label": "b", "precision": [1, 0.5], "recall": [0, 1], "ap": 0.6}],
}
REGRESSION = {
    "metrics": {"rmse": 1.0, "mae": 0.8, "r2": -0.2},
    "residual_hist": {"edges": [-1, 0, 1], "counts": [3, 4]},
    "sample": {"y_true": [1, 2], "y_pred": [1.1, 1.9]},
}
IMPORTANCE = {"method": "shap", "features": [{"name": "x", "importance": 0.1}, {"name": "y", "importance": 0.9, "std": 0.1}]}
TABLE = {"columns": ["a", "b"], "rows": [[1, "x"], [None, True]], "truncated": False}
VALID = {"classification": CLASSIFICATION, "regression": REGRESSION, "importance": IMPORTANCE, "table": TABLE}


def _with(kind, path, value):
    data = copy.deepcopy(VALID[kind])
    target = data
    for part in path[:-1]:
        target = target[part]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


@pytest.mark.parametrize("kind", sorted(VALID))
def test_valid_reports(kind):
    assert validate_report(kind, copy.deepcopy(VALID[kind]))


def test_normalization():
    assert [f["name"] for f in validate_report("importance", IMPORTANCE)["features"]] == ["y", "x"]
    long = validate_report("table", {"columns": ["c"], "rows": [["z" * 3000]]})
    assert long["truncated"] is True and len(long["rows"][0][0]) == 2000
    assert validate_report("custom", {"anything": [1, {"x": None}]}) == {"anything": [1, {"x": None}]}


INVALID = [
    ("classification", ["confusion_matrix"], [[1, 2]]),
    ("classification", ["labels"], ["a", "a"]),
    ("classification", ["labels"], []),
    ("classification", ["per_class", 0, "label"], "zzz"),
    ("classification", ["per_class", 0, "precision"], 1.5),
    ("classification", ["metrics", "accuracy"], float("nan")),
    ("classification", ["roc", 0, "tpr"], [0]),
    ("classification", ["roc", 0, "fpr"], [0.1] * 201),
    ("classification", ["pr"], [{"label": "a", "precision": [1], "recall": [0, 1], "ap": 0.5}]),
    ("classification", ["extra"], 1),
    ("regression", ["metrics", "rmse"], KeyError),
    ("regression", ["metrics", "mae"], float("inf")),
    ("regression", ["residual_hist", "edges"], [0, 1]),
    ("regression", ["residual_hist", "edges"], [1, 0, -1]),
    ("regression", ["sample", "y_pred"], [1]),
    ("regression", ["sample", "y_true"], [0.0] * 1001),
    ("importance", ["method"], "gain"),
    ("importance", ["features"], []),
    ("importance", ["features"], [{"name": f"f{i}", "importance": 1} for i in range(101)]),
    ("importance", ["features"], [{"name": "x", "importance": 1}, {"name": "x", "importance": 2}]),
    ("importance", ["features", 0, "importance"], float("nan")),
    ("table", ["rows"], [[1]]),
    ("table", ["rows"], [[1, 2]] * 1001),
    ("table", ["rows"], [[{"nested": 1}, 2]]),
    ("table", ["rows"], [[float("nan"), 2]]),
    ("table", ["columns"], []),
]


@pytest.mark.parametrize(("kind", "path", "value"), INVALID)
def test_invalid_reports(kind, path, value):
    with pytest.raises(ReportError) as err:
        validate_report(kind, _with(kind, path, value))
    assert err.value.errors and all("input" not in e for e in err.value.errors)


def test_custom_and_size_limits():
    with pytest.raises(ReportError):
        validate_report("custom", {"x": [1, float("nan")]})
    with pytest.raises(ReportError):
        validate_report("custom", {"x": "y" * (2 * 1024 * 1024)})
    with pytest.raises(ReportError):
        validate_report("nope", {})
    with pytest.raises(ReportError):
        validate_report("classification", "not an object")
