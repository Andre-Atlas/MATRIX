"""Tiny scikit-learn run tracked in MATRIX (autolog + test accuracy + a table report).

    MATRIX_API_KEY=mx_... MATRIX_ENDPOINT=http://localhost:8000 uv run python examples/train_sklearn.py
"""

from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

import matrix_sdk as mx
from matrix_sdk.integrations import sklearn as mx_sklearn

X, y = load_iris(return_X_y=True)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=0)

with mx.start_run(name="iris-rf", group="examples", tags=["sklearn"], config={"test_size": 0.3}) as run:
    mx_sklearn.autolog(run)  # params, fit time and train score of every top-level fit()
    model = RandomForestClassifier(n_estimators=50, max_depth=4, random_state=0).fit(X_train, y_train)
    run.log({"test_accuracy": model.score(X_test, y_test)})
    preds = model.predict(X_test)
    run.log_table("test_predictions", ["y_true", "y_pred"], list(zip(y_test.tolist(), preds.tolist())))
    mx_sklearn.autolog(disable=True)
    print(f"run {run.id} ({run.name})")
