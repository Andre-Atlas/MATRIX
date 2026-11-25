"""Reconciliacao T5-T8: sweeps, registry/webhooks/lineage, sandboxes/benchmarks simulated, gateway/safety (ADR-013)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19 13:00:00

Composes the parallel task fragments:
- t5_sweeps: `sweeps` and `sweep_runs` tables + data copy from `mlops_sweeps`
- t6_registry: `model_stage_history`, `model_webhooks`, `webhook_deliveries`, unique production partial index, lineage indexes
- t7_sandboxes_benchmarks: `simulated` columns on benchmark/sandbox tables + wipe plaintext `api_key`
- t8_gateway_safety_vectors: `gateway_rate_windows`, `gateway_requests`, `safety_scans.simulated` + nullable `global_score`
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FRAGMENTS = (
    "t5_sweeps",
    "t6_registry",
    "t7_sandboxes_benchmarks",
    "t8_gateway_safety_vectors",
)


def _load_fragment(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "fragments" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"matrix_migration_fragment_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration fragment: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    for name in _FRAGMENTS:
        _load_fragment(name).upgrade()


def downgrade() -> None:
    for name in reversed(_FRAGMENTS):
        _load_fragment(name).downgrade()
