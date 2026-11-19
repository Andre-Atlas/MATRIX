"""Integrations against the real libraries (tiny CPU runs); HTTP captured by MockTransport (conftest)."""

import importlib
import math
import sys

import pytest

RUN = "/api/v1/runs/r1"


def by_key(server):
    out: dict[str, list] = {}
    for p in server.points():
        out.setdefault(p["key"], []).append((p["step"], p["value"]))
    return out


@pytest.mark.parametrize(("module", "extra"), [("xgboost", "xgboost"), ("lightgbm", "lightgbm"),
                                                ("transformers", "hf"), ("sklearn", "sklearn")])
def test_missing_library_names_the_extra(monkeypatch, module, extra):
    dep = "sklearn.base" if module == "sklearn" else module
    monkeypatch.setitem(sys.modules, dep, None)
    monkeypatch.delitem(sys.modules, f"matrix_sdk.integrations.{module}", raising=False)
    with pytest.raises(ImportError, match=rf"matrix-sdk\[{extra}\]"):
        importlib.import_module(f"matrix_sdk.integrations.{module}")
    monkeypatch.delitem(sys.modules, f"matrix_sdk.integrations.{module}", raising=False)


def test_import_matrix_sdk_does_not_import_frameworks():
    import subprocess

    code = ("import sys, matrix_sdk, matrix_sdk.tracking, matrix_sdk.system; "
            "print([m for m in ('numpy','torch','transformers','sklearn','xgboost','lightgbm') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# -- scikit-learn


def test_sklearn_autolog_real_random_forest(server, make_run):
    from sklearn.base import BaseEstimator
    from sklearn.datasets import make_classification
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.tree import DecisionTreeClassifier

    from matrix_sdk.integrations import sklearn as mx_sklearn

    original = RandomForestClassifier.fit
    run = make_run()
    mx_sklearn.autolog(run)
    mx_sklearn.autolog(run)  # idempotent
    try:
        wrapped = RandomForestClassifier.fit
        assert wrapped is not original and wrapped.__matrix_original__ is original
        X, y = make_classification(n_samples=80, n_features=5, random_state=0)
        RandomForestClassifier(n_estimators=7, max_depth=3, random_state=0).fit(X, y)
        run.finish()
    finally:
        mx_sklearn.autolog(disable=True)
    assert RandomForestClassifier.fit is original
    assert not hasattr(DecisionTreeClassifier.fit, "__matrix_original__")
    assert "fit" not in BaseEstimator.__dict__
    params = server.calls("POST", f"{RUN}/params")[0][3]["params"]
    assert params["estimator"] == "RandomForestClassifier" and params["n_estimators"] == 7
    assert params["max_depth"] == 3
    m = by_key(server)
    assert set(m) == {"fit_time_s", "train_score"}  # the 7 inner tree fits are not logged
    assert len(m["fit_time_s"]) == 1 and m["fit_time_s"][0][1] > 0
    assert 0.5 < m["train_score"][0][1] <= 1.0


def test_sklearn_autolog_unsupervised_and_current_run(server, make_run):
    import numpy as np
    from sklearn.cluster import KMeans

    from matrix_sdk.integrations import sklearn as mx_sklearn

    run = make_run()  # becomes the current run
    mx_sklearn.autolog()
    try:
        KMeans(n_clusters=2, n_init=1, random_state=0).fit(np.random.default_rng(0).normal(size=(20, 2)))
        run.finish()
    finally:
        mx_sklearn.autolog(disable=True)
    assert set(by_key(server)) == {"fit_time_s"}  # no y: no train score


# -- xgboost / lightgbm


def test_xgboost_callback_real_training(server, make_run):
    import numpy as np
    import xgboost as xgb

    from matrix_sdk.integrations.xgboost import MatrixCallback

    rng = np.random.default_rng(0)
    X = rng.normal(size=(100, 4))
    y = (X[:, 0] > 0).astype(int)
    dtrain, dvalid = xgb.DMatrix(X[:80], y[:80]), xgb.DMatrix(X[80:], y[80:])
    run = make_run()
    xgb.train({"objective": "binary:logistic", "eval_metric": ["logloss", "error"], "max_depth": 2},
              dtrain, num_boost_round=5, evals=[(dtrain, "train"), (dvalid, "valid")], verbose_eval=False,
              callbacks=[MatrixCallback(run)])
    run.finish()
    m = by_key(server)
    assert set(m) == {"train/logloss", "train/error", "valid/logloss", "valid/error"}
    assert [s for s, _ in m["valid/logloss"]] == [0, 1, 2, 3, 4]
    assert m["train/logloss"][-1][1] < m["train/logloss"][0][1]


def test_lightgbm_callback_real_training(server, make_run):
    import lightgbm as lgb
    import numpy as np

    from matrix_sdk.integrations.lightgbm import callback

    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 4))
    y = X[:, 0] * 2 + rng.normal(scale=0.1, size=200)
    train = lgb.Dataset(X[:150], y[:150])
    valid = lgb.Dataset(X[150:], y[150:], reference=train)
    run = make_run()
    lgb.train({"objective": "regression", "metric": ["l2", "l1"], "verbose": -1, "num_leaves": 4},
              train, num_boost_round=5, valid_sets=[valid], valid_names=["valid"], callbacks=[callback(run)])
    run.finish()
    m = by_key(server)
    assert set(m) == {"valid/l2", "valid/l1"}
    assert [s for s, _ in m["valid/l2"]] == [0, 1, 2, 3, 4]


# -- pytorch


def test_pytorch_watch_real_loop(server, make_run):
    torch = pytest.importorskip("torch")
    from matrix_sdk.integrations.pytorch import log_epoch, watch

    torch.manual_seed(0)
    model = torch.nn.Sequential(torch.nn.Linear(4, 8), torch.nn.ReLU(), torch.nn.Linear(8, 1))
    opt = torch.optim.SGD(model.parameters(), lr=0.1)
    run = make_run()
    watcher = watch(model, run, log_freq=1, top_k=1)
    X, y = torch.randn(16, 4), torch.randn(16, 1)
    for _ in range(3):
        loss = torch.nn.functional.mse_loss(model(X), y)
        loss.backward()
        logged = watcher.step()
        opt.step()
        opt.zero_grad()
        run.log({"loss": loss})  # 0-d tensor accepted
    log_epoch(run, 0, val_loss=torch.tensor(0.5))
    run.finish()
    expected = math.sqrt(sum(float(v) ** 2 for k, v in logged.items() if k.startswith("grad/layer/")))
    assert expected <= logged["grad/global_norm"] + 1e-9
    m = by_key(server)
    assert [s for s, _ in m["grad/global_norm"]] == [0, 1, 2]
    assert [s for s, _ in m["params/global_norm"]] == [0, 1, 2]
    layer_keys = [k for k in m if k.startswith("grad/layer/")]
    assert layer_keys and set(layer_keys) <= {"grad/layer/0", "grad/layer/2"}
    assert len(m["loss"]) == 3 and m["val_loss"] == [(0, 0.5)] and m["epoch"] == [(0, 0.0)]


def test_pytorch_watch_log_freq(server, make_run):
    torch = pytest.importorskip("torch")
    from matrix_sdk.integrations.pytorch import watch

    model = torch.nn.Linear(2, 1)
    run = make_run()
    w = watch(model, run, log_freq=2)
    for _ in range(5):
        model(torch.ones(1, 2)).sum().backward()
        w.step()
    run.finish()
    assert [s for s, _ in by_key(server)["grad/global_norm"]] == [0, 2, 4]


# -- transformers


def test_transformers_logs_to_metrics_dpo_grpo_keys():
    from matrix_sdk.integrations.transformers import logs_to_metrics

    logs = {"loss": 0.6931, "learning_rate": 5e-7, "grad_norm": 3.2, "epoch": 0.25,
            "rewards/chosen": 0.12, "rewards/rejected": -0.08, "rewards/margins": 0.2,
            "rewards/accuracies": 0.75, "logps/chosen": -120.5, "eval_loss": 1000.0,
            "completions/mean_length": 87, "kl": float("nan"), "note": "text", "flag": True}
    m = logs_to_metrics(logs)
    for k in ("rewards/chosen", "rewards/rejected", "rewards/margins", "rewards/accuracies", "logps/chosen",
              "completions/mean_length", "learning_rate", "grad_norm", "epoch"):
        assert m[k] == float(logs[k])
    assert m["perplexity"] == pytest.approx(2.0, rel=1e-3)
    assert m["eval_perplexity"] == "inf"  # exp(1000) overflows: guarded
    assert m["kl"] == "nan" and "note" not in m and m["flag"] == 1.0


def test_transformers_real_trainer(server, make_run, tmp_path):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("accelerate")
    from matrix_sdk.integrations.transformers import MatrixCallback

    torch.manual_seed(0)
    config = transformers.BertConfig(vocab_size=32, hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
                                     intermediate_size=32, max_position_embeddings=16, num_labels=2)
    model = transformers.BertForSequenceClassification(config)

    class DS(torch.utils.data.Dataset):
        def __len__(self):
            return 16

        def __getitem__(self, i):
            ids = torch.randint(1, 32, (8,), generator=torch.Generator().manual_seed(i))
            return {"input_ids": ids, "attention_mask": torch.ones(8, dtype=torch.long), "labels": i % 2}

    args = transformers.TrainingArguments(
        output_dir=str(tmp_path / "out"), max_steps=4, logging_steps=1, eval_strategy="steps", eval_steps=2,
        per_device_train_batch_size=4, per_device_eval_batch_size=8, report_to="none", use_cpu=True,
        save_strategy="no", disable_tqdm=True, learning_rate=1e-3,
    )
    run = make_run()
    trainer = transformers.Trainer(model=model, args=args, train_dataset=DS(), eval_dataset=DS(),
                                   callbacks=[MatrixCallback(run)])
    trainer.train()
    run.finish()
    params = server.calls("POST", f"{RUN}/params")[0][3]["params"]
    assert params["learning_rate"] == 1e-3 and params["max_steps"] == 4 and params["model_type"] == "bert"
    m = by_key(server)
    assert [s for s, _ in m["loss"]] == [1, 2, 3, 4]
    assert {"learning_rate", "grad_norm", "epoch", "perplexity", "eval_loss", "eval_perplexity"} <= set(m)
    assert m["perplexity"][0][1] == pytest.approx(math.exp(m["loss"][0][1]), rel=1e-6)
    assert [s for s, _ in m["eval_loss"]] == [2, 4]
