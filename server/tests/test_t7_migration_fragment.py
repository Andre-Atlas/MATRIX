"""Tests for the T7 migration fragment (`t7_sandboxes_benchmarks.py`)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from matrix_server.storage.db import make_engine, utcnow

FRAGMENT = Path(__file__).resolve().parents[1] / "migrations" / "fragments" / "t7_sandboxes_benchmarks.py"


def _load_fragment():
    spec = importlib.util.spec_from_file_location("t7_sandboxes_benchmarks", FRAGMENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_0008_subset(engine: sa.Engine) -> None:
    """Create the pre-T7 (revision 0007/0008) schema for the 4 affected tables."""
    md = sa.MetaData()
    sa.Table(
        "benchmark_runs",
        md,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("suite", sa.String(32), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("runner_backend", sa.String(32), nullable=False, server_default="local_sim"),
        sa.Column("runner_config", sa.JSON(), nullable=False),
        sa.Column("eval_mode", sa.String(32), nullable=False, server_default="generate_until"),
        sa.Column("num_fewshot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_model", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("summary_metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "benchmark_task_results",
        md,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("benchmark_run_id", sa.String(32), sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False),
        sa.Column("primary_metric", sa.String(100), nullable=False),
        sa.Column("primary_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "sandbox_runs",
        md,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("runner_backend", sa.String(32), nullable=False, server_default="local_sandbox"),
        sa.Column("solver_config", sa.JSON(), nullable=False),
        sa.Column("scorer_config", sa.JSON(), nullable=False),
        sa.Column("sandbox_policy", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "sandbox_task_transcripts",
        md,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("sandbox_run_id", sa.String(32), sa.ForeignKey("sandbox_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="passed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    md.create_all(engine)


def test_t7_fragment_upgrade_wipes_api_key_and_downgrade(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't7_mig.db'}")
    try:
        _create_0008_subset(engine)
        now = utcnow()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO benchmark_runs (id, project_id, name, suite, tasks, runner_backend, "
                    "runner_config, eval_mode, num_fewshot, target_model, status, summary_metrics, created_at) "
                    "VALUES (:id, 'p1', 'r1', 'lm_eval', '[\"gsm8k\"]', 'http', :cfg, 'generate_until', 0, 'm', 'completed', '{}', :ts)"
                ),
                {"id": "br-http", "cfg": '{"endpoint_url": "https://api.example.com/v1", "api_key": "sk-secret-123"}', "ts": now},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO benchmark_runs (id, project_id, name, suite, tasks, runner_backend, "
                    "runner_config, eval_mode, num_fewshot, target_model, status, summary_metrics, created_at) "
                    "VALUES (:id, 'p1', 'r2', 'lm_eval', '[\"gsm8k\"]', 'local_sim', '{}', 'generate_until', 0, 'm', 'completed', '{}', :ts)"
                ),
                {"id": "br-sim", "ts": now},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO benchmark_task_results (id, project_id, benchmark_run_id, task_name, "
                    "primary_metric, primary_score, metrics, created_at) "
                    "VALUES ('btr-1', 'p1', 'br-sim', 'gsm8k', 'exact_match', 0.8, '{}', :ts)"
                ),
                {"ts": now},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sandbox_runs (id, project_id, name, runner_backend, solver_config, "
                    "scorer_config, sandbox_policy, status, created_at) "
                    "VALUES ('sr-1', 'p1', 's1', 'fake', '{}', '{}', '{}', 'completed', :ts)"
                ),
                {"ts": now},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sandbox_task_transcripts (id, project_id, sandbox_run_id, task_key, status, created_at) "
                    "VALUES ('stt-1', 'p1', 'sr-1', 't1', 'passed', :ts)"
                ),
                {"ts": now},
            )

        fragment = _load_fragment()
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            fragment.upgrade()

        insp = sa.inspect(engine)
        for tbl in fragment.TABLES:
            cols = {c["name"] for c in insp.get_columns(tbl)}
            assert "simulated" in cols, f"missing simulated column in {tbl}"

        with engine.connect() as conn:
            http_row = conn.execute(
                sa.text("SELECT runner_config, simulated FROM benchmark_runs WHERE id = 'br-http'")
            ).one()
            assert "sk-secret-123" not in str(http_row[0])
            assert "api_key" not in str(http_row[0])
            assert "https://api.example.com/v1" in str(http_row[0])
            assert bool(http_row[1]) is False

            sim_row = conn.execute(
                sa.text("SELECT simulated FROM benchmark_runs WHERE id = 'br-sim'")
            ).one()
            assert bool(sim_row[0]) is True

            btr_row = conn.execute(
                sa.text("SELECT simulated FROM benchmark_task_results WHERE id = 'btr-1'")
            ).one()
            assert bool(btr_row[0]) is True

            sr_row = conn.execute(
                sa.text("SELECT simulated FROM sandbox_runs WHERE id = 'sr-1'")
            ).one()
            assert bool(sr_row[0]) is True

            stt_row = conn.execute(
                sa.text("SELECT simulated FROM sandbox_task_transcripts WHERE id = 'stt-1'")
            ).one()
            assert bool(stt_row[0]) is True

        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            fragment.downgrade()

        insp = sa.inspect(engine)
        for tbl in fragment.TABLES:
            cols = {c["name"] for c in insp.get_columns(tbl)}
            assert "simulated" not in cols, f"simulated column not dropped from {tbl}"
    finally:
        engine.dispose()
