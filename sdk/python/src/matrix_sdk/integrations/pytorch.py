"""PyTorch: gradient/parameter norms every `log_freq` steps.

    watcher = watch(model, run, log_freq=100)
    for batch in loader:
        loss = ...; loss.backward()
        watcher.step()          # after backward, before optimizer.zero_grad()
        optimizer.step(); optimizer.zero_grad()
"""

from __future__ import annotations

import math
from typing import Any

from ..tracking import Run, resolve_run, safe_key
from . import require


def _layer(param_name: str) -> str:
    return param_name.rsplit(".", 1)[0] if "." in param_name else param_name


class Watcher:
    """Logs `grad/global_norm`, `params/global_norm` and `grad/layer/<name>` for the top-k layers."""

    def __init__(self, model: Any, run: Run | None, log_freq: int = 100, top_k: int = 5) -> None:
        if log_freq < 1:
            raise ValueError("log_freq must be >= 1")
        self.model = model
        self._run = run
        self.log_freq = log_freq
        self.top_k = top_k
        self.steps = 0

    def step(self, step: int | None = None) -> dict[str, float] | None:
        """Call once per optimizer step, after `loss.backward()`. Returns what was logged (or None)."""
        current = self.steps if step is None else int(step)
        self.steps += 1
        if current % self.log_freq:
            return None
        run = resolve_run(self._run)
        if run is None:
            return None
        metrics = self.norms()
        run.log(metrics, step=current)
        return metrics

    def norms(self) -> dict[str, float]:
        torch = require("torch", "torch")
        grad_sq = 0.0
        param_sq = 0.0
        layers: dict[str, float] = {}
        with torch.no_grad():
            for name, p in self.model.named_parameters():
                param_sq += float(p.detach().float().pow(2).sum())
                if p.grad is None:
                    continue
                g = float(p.grad.detach().float().pow(2).sum())
                grad_sq += g
                layer = _layer(name)
                layers[layer] = layers.get(layer, 0.0) + g
        out = {"grad/global_norm": math.sqrt(grad_sq), "params/global_norm": math.sqrt(param_sq)}
        for layer, sq in sorted(layers.items(), key=lambda kv: kv[1], reverse=True)[: self.top_k]:
            out[safe_key(f"grad/layer/{layer}")] = math.sqrt(sq)
        return out


def watch(model: Any, run: Run | None = None, log_freq: int = 100, top_k: int = 5) -> Watcher:
    require("torch", "torch")
    return Watcher(model, run, log_freq=log_freq, top_k=top_k)


def log_epoch(run: Run | None, epoch: int, *, step: int | None = None, **metrics: Any) -> None:
    """Logs `epoch` plus the given metrics at step = epoch (or `step`)."""
    target = resolve_run(run)
    if target is None:
        return
    target.log({"epoch": epoch, **metrics}, step=epoch if step is None else step)
