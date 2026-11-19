"""Tiny PyTorch regression loop tracked in MATRIX (metrics, gradient norms, final artifact).

    MATRIX_API_KEY=mx_... MATRIX_ENDPOINT=http://localhost:8000 uv run python examples/train_pytorch.py
"""

import tempfile
from pathlib import Path

import torch

import matrix_sdk as mx
from matrix_sdk.integrations.pytorch import log_epoch, watch

config = {"lr": 0.05, "epochs": 3, "batch_size": 32, "hidden": 16}
torch.manual_seed(0)
X = torch.randn(256, 4)
y = X @ torch.tensor([[1.0], [-2.0], [0.5], [0.0]]) + 0.1 * torch.randn(256, 1)

model = torch.nn.Sequential(torch.nn.Linear(4, config["hidden"]), torch.nn.ReLU(),
                            torch.nn.Linear(config["hidden"], 1))
opt = torch.optim.Adam(model.parameters(), lr=config["lr"])

with mx.start_run(name="toy-regression", group="examples", tags=["pytorch"], config=config) as run:
    watcher = watch(model, run, log_freq=4)
    for epoch in range(config["epochs"]):
        for i in range(0, len(X), config["batch_size"]):
            loss = torch.nn.functional.mse_loss(model(X[i:i + config["batch_size"]]), y[i:i + config["batch_size"]])
            loss.backward()
            watcher.step()
            opt.step()
            opt.zero_grad()
            run.log({"train/loss": loss})  # automatic step counter
        with torch.no_grad():
            log_epoch(run, epoch, val_loss=torch.nn.functional.mse_loss(model(X), y))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.pt"
        torch.save(model.state_dict(), path)
        run.log_artifact(path, kind="model")
    print(f"run {run.id} ({run.name}), dropped points: {run.dropped_points}")
