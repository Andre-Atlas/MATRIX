"""T5 (canonical sweeps over `runs`) migration fragment — not a revision itself.

Alembic only loads `migrations/versions/`; the coordinator revision (0009+) calls `upgrade()` /
`downgrade()` from here. Matches `matrix_server.sweeps.models` (`sweeps` and `sweep_runs`),
copies existing rows from `mlops_sweeps` (converting legacy config/status shapes to G2) and
backfills `sweep_runs` from `runs` linked via `runs.sweep_id`.
Requires `projects`, `runs`, and `mlops_sweeps` (present at revision 0008).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

TABLES = ("sweeps", "sweep_runs")


def _as_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))

_STATUS_MAP = {
    "running": "running",
    "paused": "paused",
    "finished": "finished",
    "completed": "finished",
    "stopped": "stopped",
    "cancelled": "stopped",
}


def _convert_search_space(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for name, spec in raw.items():
        if isinstance(spec, dict) and spec.get("type") == "categorical":
            out[name] = {**spec, "type": "choice"}
        elif isinstance(spec, dict):
            out[name] = dict(spec)
        else:
            out[name] = spec
    return out


def _convert_early_stop(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None
    strat = str(raw.get("strategy") or raw.get("type") or raw.get("method") or "none").lower()
    if strat == "none":
        return None
    out: dict[str, Any] = {
        "type": strat,
        "min_iter": int(raw.get("min_iter", raw.get("min_step", raw.get("min_t", 10)))),
        "eta": int(raw.get("eta", raw.get("reduction_factor", 3))),
    }
    if raw.get("max_iter") is not None:
        out["max_iter"] = int(raw["max_iter"])
    return out


def upgrade() -> None:
    op.create_table(
        "sweeps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("metric_key", sa.String(length=200), nullable=False),
        sa.Column("metric_goal", sa.String(length=16), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("best_run_id", sa.String(length=32), nullable=True),
        sa.Column("best_value", sa.Float(), nullable=True),
        sa.Column("next_index", sa.Integer(), nullable=False),
        sa.Column("importance_cache", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sweeps_project_id", "sweeps", ["project_id"])
    op.create_index("ix_sweeps_project_status", "sweeps", ["project_id", "status"])
    op.create_index("ix_sweeps_project_created", "sweeps", ["project_id", "created_at", "id"])

    op.create_table(
        "sweep_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("sweep_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("trial_index", sa.Integer(), nullable=False),
        sa.Column("bracket", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=True),
        sa.Column("pruned", sa.Boolean(), nullable=False),
        sa.Column("last_step", sa.BigInteger(), nullable=True),
        sa.Column("last_value", sa.Float(), nullable=True),
        sa.Column("best_value", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sweep_id"], ["sweeps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sweep_id", "trial_index", name="uq_sweep_runs_sweep_index"),
        sa.UniqueConstraint("sweep_id", "run_id", name="uq_sweep_runs_sweep_run"),
    )
    op.create_index("ix_sweep_runs_sweep_id", "sweep_runs", ["sweep_id"])
    op.create_index("ix_sweep_runs_project_id", "sweep_runs", ["project_id"])
    op.create_index("ix_sweep_runs_run_id", "sweep_runs", ["run_id"])
    op.create_index("ix_sweep_runs_sweep_bracket", "sweep_runs", ["sweep_id", "bracket"])

    # Copy existing rows from mlops_sweeps (if present) and backfill sweep_runs from runs.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())
    if "mlops_sweeps" not in existing_tables:
        return

    valid_projects = {row[0] for row in conn.execute(sa.text("SELECT id FROM projects")).fetchall()}
    legacy_rows = conn.execute(
        sa.text(
            "SELECT id, project_id, name, description, method, metric_name, metric_goal, "
            "search_space, early_stopping, max_trials, status, best_run_id, best_metric_value, "
            "created_at, stopped_at FROM mlops_sweeps"
        )
    ).mappings().all()

    sweeps_tbl = sa.table(
        "sweeps",
        sa.column("id", sa.String(32)),
        sa.column("project_id", sa.String(32)),
        sa.column("name", sa.String(200)),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String(16)),
        sa.column("config", sa.JSON()),
        sa.column("metric_key", sa.String(200)),
        sa.column("metric_goal", sa.String(16)),
        sa.column("seed", sa.Integer()),
        sa.column("best_run_id", sa.String(32)),
        sa.column("best_value", sa.Float()),
        sa.column("next_index", sa.Integer()),
        sa.column("importance_cache", sa.JSON()),
        sa.column("created_by", sa.String(64)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("stopped_at", sa.DateTime(timezone=True)),
    )
    sweep_runs_tbl = sa.table(
        "sweep_runs",
        sa.column("id", sa.String(32)),
        sa.column("sweep_id", sa.String(32)),
        sa.column("project_id", sa.String(32)),
        sa.column("run_id", sa.String(32)),
        sa.column("trial_index", sa.Integer()),
        sa.column("bracket", sa.Integer()),
        sa.column("agent_id", sa.String(128)),
        sa.column("pruned", sa.Boolean()),
        sa.column("last_step", sa.BigInteger()),
        sa.column("last_value", sa.Float()),
        sa.column("best_value", sa.Float()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    has_runs = "runs" in existing_tables
    for row in legacy_rows:
        pid = row["project_id"]
        if pid not in valid_projects:
            continue
        method = "tpe" if row["method"] == "bayes" else (row["method"] or "random")
        goal = row["metric_goal"] if row["metric_goal"] in ("minimize", "maximize") else "minimize"
        config: dict[str, Any] = {
            "method": method,
            "metric": {"key": row["metric_name"], "goal": goal},
            "parameters": _convert_search_space(row["search_space"]),
        }
        es = _convert_early_stop(row["early_stopping"])
        if es is not None:
            config["early_stop"] = es
        if row["max_trials"] is not None:
            config["max_runs"] = int(row["max_trials"])

        linked_runs: list[Any] = []
        if has_runs:
            linked_runs = list(
                conn.execute(
                    sa.text(
                        "SELECT id, started_at FROM runs "
                        "WHERE project_id = :pid AND sweep_id = :sid "
                        "ORDER BY started_at ASC, id ASC"
                    ),
                    {"pid": pid, "sid": row["id"]},
                ).mappings().all()
            )

        conn.execute(
            sweeps_tbl.insert().values(
                id=row["id"],
                project_id=pid,
                name=row["name"],
                description=row["description"],
                status=_STATUS_MAP.get(str(row["status"] or "running"), "running"),
                config=config,
                metric_key=row["metric_name"],
                metric_goal=goal,
                seed=None,
                best_run_id=row["best_run_id"],
                best_value=row["best_metric_value"],
                next_index=len(linked_runs),
                importance_cache=None,
                created_by=None,
                created_at=_as_dt(row["created_at"]),
                stopped_at=_as_dt(row["stopped_at"]),
            )
        )

        for idx, run_row in enumerate(linked_runs):
            conn.execute(
                sweep_runs_tbl.insert().values(
                    id=f"sr{run_row['id'][:30]}",
                    sweep_id=row["id"],
                    project_id=pid,
                    run_id=run_row["id"],
                    trial_index=idx,
                    bracket=0,
                    agent_id=None,
                    pruned=False,
                    last_step=None,
                    last_value=None,
                    best_value=None,
                    created_at=_as_dt(run_row["started_at"]),
                )
            )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
