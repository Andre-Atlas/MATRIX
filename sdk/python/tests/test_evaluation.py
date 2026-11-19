"""matrix_sdk.evaluation against real scikit-learn on small synthetic data; HTTP captured by conftest."""

import math
import subprocess
import sys
import warnings

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
from sklearn.datasets import make_classification, make_regression
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import roc_auc_score

from matrix_sdk import evaluation as ev

REPORTS = "/api/v1/runs/r1/reports"


# -- mirror of the server schema (contract G1) -----------------------------------------------------------


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_classification(d, n):
    assert set(d) >= {"labels", "metrics", "per_class", "confusion_matrix", "roc", "pr"}
    k = len(d["labels"])
    assert all(isinstance(x, str) for x in d["labels"])
    assert all(num(v) for v in d["metrics"].values()) and "accuracy" in d["metrics"]
    assert len(d["per_class"]) == k
    for pc in d["per_class"]:
        assert set(pc) == {"label", "precision", "recall", "f1", "support"} and pc["label"] in d["labels"]
    assert sum(pc["support"] for pc in d["per_class"]) == n
    cm = d["confusion_matrix"]
    assert len(cm) == k and all(len(r) == k for r in cm) and sum(map(sum, cm)) == n
    for c in d["roc"]:
        assert set(c) == {"label", "fpr", "tpr", "auc"} and len(c["fpr"]) == len(c["tpr"]) <= 200
        assert c["fpr"][0] == 0 and c["tpr"][0] == 0 and c["fpr"][-1] == 1 and c["tpr"][-1] == 1
        assert c["fpr"] == sorted(c["fpr"]) and c["tpr"] == sorted(c["tpr"]) and num(c["auc"])
    for c in d["pr"]:
        assert set(c) == {"label", "precision", "recall", "ap"} and len(c["precision"]) == len(c["recall"]) <= 200
        assert num(c["ap"])


def validate_importance(d):
    assert d["method"] in ("shap", "permutation", "model")
    f = d["features"]
    assert 0 < len(f) <= 100
    assert [x["importance"] for x in f] == sorted((x["importance"] for x in f), reverse=True)
    assert all(isinstance(x["name"], str) and num(x["importance"]) for x in f)


@pytest.fixture(scope="module")
def binary():
    X, y = make_classification(n_samples=400, n_features=8, n_informative=4, random_state=0)
    return X, y, LogisticRegression(max_iter=500).fit(X, y)


@pytest.fixture(scope="module")
def multi():
    X, y = make_classification(n_samples=500, n_features=10, n_informative=6, n_classes=4, random_state=1)
    return X, y, RandomForestClassifier(n_estimators=20, random_state=0).fit(X, y)


# -- classification --------------------------------------------------------------------------------------


def test_binary(binary):
    X, y, m = binary
    p = m.predict_proba(X)[:, 1]
    d = ev.classification_report_data(y, y_score=p)
    validate_classification(d, len(y))
    assert d["labels"] == ["0", "1"] and len(d["roc"]) == 1 and d["roc"][0]["label"] == "1"
    assert abs(d["roc"][0]["auc"] - roc_auc_score(y, p)) < 1e-6
    assert abs(d["metrics"]["roc_auc"] - roc_auc_score(y, p)) < 1e-6
    assert {"balanced_accuracy", "f1_macro", "f1_weighted", "log_loss", "matthews_corrcoef", "cohen_kappa",
            "average_precision"} <= set(d["metrics"])


def test_multiclass(multi):
    X, y, m = multi
    P = m.predict_proba(X)
    d = ev.classification_report_data(y, y_score=P)
    validate_classification(d, len(y))
    assert len(d["roc"]) == 4 and len(d["pr"]) == 4
    for i, c in enumerate(d["roc"]):
        assert abs(c["auc"] - roc_auc_score(y == i, P[:, i])) < 1e-6
    assert abs(d["metrics"]["roc_auc"] - roc_auc_score(y, P, multi_class="ovr")) < 1e-6


def test_downsampling_to_200_and_shape():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 5000)
    s = np.clip(y * 0.3 + rng.random(5000) * 0.7, 0, 1)
    d = ev.classification_report_data(y, y_score=s)
    validate_classification(d, 5000)
    c = d["roc"][0]
    assert 50 < len(c["fpr"]) <= 200
    # trapezoid area of the thinned curve stays close to the true AUC
    area = np.trapezoid(c["tpr"], c["fpr"])
    assert abs(area - roc_auc_score(y, s)) < 5e-3


def test_pred_only_skips_curves(binary):
    X, y, m = binary
    d = ev.classification_report_data(y, y_pred=m.predict(X))
    validate_classification(d, len(y))
    assert d["roc"] == [] and d["pr"] == []
    assert not {"roc_auc", "average_precision", "log_loss"} & set(d["metrics"])


def test_class_missing_from_pred(multi):
    _X, y, _ = multi
    pred = np.where(y == 3, 0, y)
    d = ev.classification_report_data(y, y_pred=pred, labels=[0, 1, 2, 3])
    validate_classification(d, len(y))
    pc = d["per_class"][3]
    assert pc["precision"] == 0 and pc["recall"] == 0 and pc["support"] == int((y == 3).sum())


def test_imbalanced():
    rng = np.random.default_rng(0)
    y = (rng.random(1000) < 0.02).astype(int)
    s = np.clip(0.2 + 0.5 * y + rng.normal(0, 0.1, 1000), 0, 1)
    d = ev.classification_report_data(y, y_score=s)
    validate_classification(d, 1000)
    assert d["metrics"]["balanced_accuracy"] <= 1


def test_single_class_warns_and_omits():
    y = np.zeros(20, dtype=int)
    with pytest.warns(UserWarning, match="undefined"):
        d = ev.classification_report_data(y, y_score=np.linspace(0.1, 0.9, 20), labels=[0, 1])
    assert d["roc"] == [] and "roc_auc" not in d["metrics"]
    validate_classification(d, 20)


def test_string_labels_and_dataframe(multi):
    pd = pytest.importorskip("pandas")
    X, y, m = multi
    names = np.array(["a", "b", "c", "d"])
    P = m.predict_proba(X)
    d = ev.classification_report_data(pd.Series(names[y]), y_score=P, labels=list(names))
    validate_classification(d, len(y))
    assert d["labels"] == ["a", "b", "c", "d"] and d["roc"][2]["label"] == "c"
    with pytest.raises(ValueError, match="not in labels"):
        ev.classification_report_data(names[y], y_pred=np.array(["z"] * len(y)), labels=list(names))


def test_nan_rejected(binary):
    X, y, m = binary
    p = m.predict_proba(X)[:, 1].copy()
    p[3] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        ev.classification_report_data(y, y_score=p)
    with pytest.raises(ValueError, match="probabilities"):
        ev.classification_report_data(y, y_score=np.full(len(y), 5.0))


def test_too_many_classes_rejected_before_sending(server, make_run):
    run = make_run()
    y = np.arange(10_000)
    with pytest.raises(ValueError, match="too many"):
        ev.log_classification_report(run, y, y_pred=y)
    run.flush() if hasattr(run, "flush") else None
    assert server.calls("POST", REPORTS) == []


def test_report_size_cap(server, make_run, monkeypatch):
    from matrix_sdk import tracking

    monkeypatch.setattr(tracking, "MAX_REPORT_BYTES", 200)
    run = make_run()
    with pytest.raises(ValueError, match="limit"):
        ev.log_classification_report(run, [0, 1, 1, 0], y_pred=[0, 1, 0, 0])
    assert server.calls("POST", REPORTS) == []


def test_log_posts_contract_body_and_metrics(server, make_run, binary):
    X, y, m = binary
    run = make_run()
    ev.log_classification_report(run, y, y_score=m.predict_proba(X)[:, 1], name="val")
    run.finish(timeout=2)
    (_, _, _, body), = server.calls("POST", REPORTS)
    assert body["kind"] == "classification" and body["name"] == "val"
    validate_classification(body["data"], len(y))
    keys = {p["key"] for p in server.points()}
    assert {"eval/val/accuracy", "eval/val/f1_macro", "eval/val/roc_auc"} <= keys
    steps = {p["step"] for p in server.points() if p["key"].startswith("eval/val/")}
    assert len(steps) == 1


def test_log_metrics_false(server, make_run, binary):
    X, y, m = binary
    run = make_run()
    ev.log_classification_report(run, y, y_pred=m.predict(X), log_metrics=False)
    run.finish(timeout=2)
    assert server.points() == [] and len(server.calls("POST", REPORTS)) == 1


# -- regression ------------------------------------------------------------------------------------------


def test_regression(server, make_run):
    X, y = make_regression(n_samples=3000, n_features=5, noise=10, random_state=0)
    pred = LinearRegression().fit(X, y).predict(X)
    d = ev.regression_report_data(y, pred)
    assert {"rmse", "mae", "r2", "median_ae", "explained_variance", "max_error"} <= set(d["metrics"])
    assert d["metrics"]["r2"] > 0.9 and ("mape" in d["metrics"]) == bool(np.all(y != 0))
    h = d["residual_hist"]
    assert len(h["edges"]) == len(h["counts"]) + 1 and 10 <= len(h["counts"]) <= 60 and sum(h["counts"]) == 3000
    assert len(d["sample"]["y_true"]) == len(d["sample"]["y_pred"]) == 1000
    assert d == ev.regression_report_data(y, pred)  # seeded
    run = make_run()
    ev.log_regression_report(run, y, pred)
    run.finish(timeout=2)
    (_, _, _, body), = server.calls("POST", REPORTS)
    assert body["kind"] == "regression" and "eval/eval/rmse" in {p["key"] for p in server.points()}


def test_regression_mape_omitted_with_zeros_and_validation():
    d = ev.regression_report_data([0.0, 1.0, 2.0], [0.1, 1.1, 2.1])
    assert "mape" not in d["metrics"]
    with pytest.raises(ValueError, match="1-D"):
        ev.regression_report_data(np.zeros((5, 2)), np.zeros((5, 2)))
    with pytest.raises(ValueError, match="NaN"):
        ev.regression_report_data([1.0, np.nan], [1.0, 2.0])


# -- importance ------------------------------------------------------------------------------------------


def test_importance_model_and_permutation_and_names(binary, multi):
    pd = pytest.importorskip("pandas")
    X, y, m = binary
    df = pd.DataFrame(X, columns=[f"col{i}" for i in range(X.shape[1])])
    d = ev.feature_importance_data(m, df, method="model")
    validate_importance(d)
    assert d["method"] == "model" and d["features"][0]["name"].startswith("col")
    d = ev.feature_importance_data(m, X, y, method="permutation")
    validate_importance(d)
    assert d["method"] == "permutation" and all("std" in f for f in d["features"])
    assert d["features"][0]["name"].startswith("f")
    d = ev.feature_importance_data(m, X, max_features=3)
    assert len(d["features"]) == 3
    Xm, _ym, rf = multi
    assert ev.feature_importance_data(rf, Xm, method="model")["method"] == "model"


def test_importance_errors(binary):
    X, y, m = binary
    with pytest.raises(ValueError, match="y"):
        ev.feature_importance_data(m, X, method="permutation")
    with pytest.raises(ValueError, match="no feature_importances_"):
        ev.feature_importance_data(object(), X, method="model")
    with pytest.raises(ValueError, match="tree models only"):
        pytest.importorskip("shap")
        ev.feature_importance_data(m, X, y, method="shap")


def test_importance_shap(multi):
    pytest.importorskip("shap")
    X, _y, rf = multi
    d = ev.feature_importance_data(rf, X)  # auto -> shap for a tree ensemble (multiclass)
    validate_importance(d)
    assert d["method"] == "shap" and len(d["features"]) == 10
    Xb, yb = make_classification(n_samples=200, n_features=6, random_state=0)
    assert ev.feature_importance_data(RandomForestClassifier(n_estimators=10, random_state=0).fit(Xb, yb),
                                      Xb, method="shap")["method"] == "shap"
    xgb = pytest.importorskip("xgboost")
    d = ev.feature_importance_data(xgb.XGBClassifier(n_estimators=10).fit(Xb, yb), Xb)
    assert d["method"] == "shap"


def test_importance_auto_falls_back_without_shap(monkeypatch, multi):
    X, _y, rf = multi
    monkeypatch.setitem(sys.modules, "shap", None)
    assert ev.feature_importance_data(rf, X)["method"] == "model"
    with pytest.raises(ImportError, match=r"matrix-sdk\[shap\]"):
        ev.feature_importance_data(rf, X, method="shap")


# -- convenience / imports -------------------------------------------------------------------------------


def test_evaluate_and_log(server, make_run, binary):
    X, y, m = binary
    run = make_run()
    out = ev.evaluate_and_log(run, m, X, y)
    assert out["task"] == "classification" and out["importance"]["method"] == "permutation"
    Xr, yr = make_regression(n_samples=200, n_features=4, random_state=0)
    out = ev.evaluate_and_log(run, RandomForestRegressor(n_estimators=10, random_state=0).fit(Xr, yr), Xr, yr)
    assert out["task"] in ("regression",) and out["importance"]["method"] in ("shap", "permutation")
    run.finish(timeout=2)
    kinds = [b["kind"] for *_, b in server.calls("POST", REPORTS)]
    assert kinds == ["classification", "importance", "regression", "importance"]


def test_missing_numpy_names_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    with pytest.raises(ImportError, match=r"matrix-sdk\[sklearn\]"):
        ev.classification_report_data([0, 1], y_pred=[0, 1])


def test_import_is_lazy():
    code = "import sys, matrix_sdk.evaluation; print('numpy' in sys.modules or 'sklearn' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_no_warnings_on_normal_input(multi):
    X, y, m = multi
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ev.classification_report_data(y, y_score=m.predict_proba(X))
