"""T7 migration fragment (sandboxes + benchmarks) — not an Alembic revision itself.

Adds `simulated` boolean columns to `benchmark_runs`, `benchmark_task_results`, `sandbox_runs`,
and `sandbox_task_transcripts`; widens `sandbox_runs.status` to 24 chars (for
`sandbox_unavailable`); updates `sandbox_runs.runner_backend` default to `'docker'`; backfills
`simulated=true` for simulated backends; and wipes any plaintext `api_key` from
`benchmark_runs.runner_config`.

Requires revision 0008 (tables created in 0007).
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

TABLES = (
    "benchmark_runs",
    "benchmark_task_results",
    "sandbox_runs",
    "sandbox_task_transcripts",
)


def _strip_api_key(raw: Any) -> tuple[dict[str, Any] | None, bool]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None, False
    elif isinstance(raw, dict):
        parsed = raw
    else:
        return None, False

    if not isinstance(parsed, dict):
        return None, False

    cleaned = {k: v for k, v in parsed.items() if str(k).lower() != "api_key"}
    if len(cleaned) == len(parsed):
        return None, False
    return cleaned, True


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        op.add_column(
            table,
            sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if bind.dialect.name != "sqlite":
        op.alter_column(
            "sandbox_runs",
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=24),
            existing_nullable=False,
            existing_server_default="queued",
        )
        op.alter_column(
            "sandbox_runs",
            "runner_backend",
            existing_type=sa.String(length=32),
            type_=sa.String(length=32),
            existing_nullable=False,
            existing_server_default="local_sandbox",
            server_default="docker",
        )

    # Backfill `simulated = true` for simulated backends
    bind.execute(
        sa.text("UPDATE benchmark_runs SET simulated = :t WHERE runner_backend = 'local_sim'"),
        {"t": True},
    )
    bind.execute(
        sa.text(
            "UPDATE benchmark_task_results SET simulated = :t "
            "WHERE benchmark_run_id IN (SELECT id FROM benchmark_runs WHERE runner_backend = 'local_sim')"
        ),
        {"t": True},
    )
    bind.execute(
        sa.text("UPDATE sandbox_runs SET simulated = :t WHERE runner_backend = 'fake'"),
        {"t": True},
    )
    bind.execute(
        sa.text(
            "UPDATE sandbox_task_transcripts SET simulated = :t "
            "WHERE sandbox_run_id IN (SELECT id FROM sandbox_runs WHERE runner_backend = 'fake')"
        ),
        {"t": True},
    )

    # Wipe plaintext `api_key` from any existing `benchmark_runs.runner_config` rows
    rows = bind.execute(sa.text("SELECT id, runner_config FROM benchmark_runs")).fetchall()
    bench_tbl = sa.table(
        "benchmark_runs",
        sa.column("id", sa.String(length=32)),
        sa.column("runner_config", sa.JSON()),
    )
    for row_id, raw_cfg in rows:
        cleaned, changed = _strip_api_key(raw_cfg)
        if changed and cleaned is not None:
            bind.execute(
                bench_tbl.update().where(bench_tbl.c.id == row_id).values(runner_config=cleaned)
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "sandbox_runs",
            "runner_backend",
            existing_type=sa.String(length=32),
            type_=sa.String(length=32),
            existing_nullable=False,
            existing_server_default="docker",
            server_default="local_sandbox",
        )
        op.alter_column(
            "sandbox_runs",
            "status",
            existing_type=sa.String(length=24),
            type_=sa.String(length=16),
            existing_nullable=False,
            existing_server_default="queued",
        )

    for table in reversed(TABLES):
        op.drop_column(table, "simulated")
