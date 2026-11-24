"""Tests for Alembic revision 0009 (`20261119_0009_reconciliacao_t5_t8.py`).

Exercises `0008 -> 0009 -> 0008 -> 0009` on both SQLite and PostgreSQL (when
`MATRIX_TEST_DATABASE_URL` is set), including `'sandbox_unavailable'` status rows
in `sandbox_runs` and `alembic.autogenerate.compare_metadata` checks.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from matrix_server.storage import models  # noqa: F401 - ensure all models registered on Base.metadata
from matrix_server.storage.db import Base, make_engine, utcnow

SERVER_DIR = Path(__file__).resolve().parents[1]
REV_0009_PATH = SERVER_DIR / "migrations" / "versions" / "20261119_0009_reconciliacao_t5_t8.py"
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


def _load_rev_0009():
    spec = importlib.util.spec_from_file_location("rev_0009_reconciliacao_t5_t8", REV_0009_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drop_all_tables(engine: sa.Engine) -> None:
    md = sa.MetaData()
    md.reflect(bind=engine)
    md.drop_all(bind=engine)


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _seed_0008_rows(conn: sa.Connection) -> None:
    now = utcnow()
    conn.execute(
        sa.text("INSERT INTO projects (id, name, created_at) VALUES ('p1', 'proj-1', :ts)"),
        {"ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO mlops_sweeps (id, project_id, name, method, metric_name, metric_goal, "
            "search_space, status, created_at) "
            "VALUES ('sw1', 'p1', 'sweep-1', 'bayes', 'loss', 'minimize', "
            "'{\"lr\": {\"type\": \"float\", \"min\": 0.001, \"max\": 0.1}}', 'running', :ts)"
        ),
        {"ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO runs (id, project_id, name, status, tags, params, sweep_id, "
            "started_at, heartbeat_at, point_count, key_counts) "
            "VALUES ('r1', 'p1', 'trial-1', 'finished', '[]', '{\"lr\": 0.01}', 'sw1', :ts, :ts, 0, '{}')"
        ),
        {"ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO mlops_registered_models (id, project_id, name, model_type, tags, created_at, updated_at) "
            "VALUES ('rm1', 'p1', 'm1', 'llm', '[]', :ts, :ts)"
        ),
        {"ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO mlops_model_versions (id, project_id, model_id, version, stage, metrics_snapshot, created_at) "
            "VALUES ('mv1', 'p1', 'rm1', 1, 'production', '{}', :ts), "
            "('mv2', 'p1', 'rm1', 2, 'production', '{}', :ts)"
        ),
        {"ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO benchmark_runs (id, project_id, name, suite, tasks, runner_backend, "
            "runner_config, eval_mode, num_fewshot, target_model, status, summary_metrics, created_at) "
            "VALUES ('br1', 'p1', 'bench-http', 'lm_eval', '[\"gsm8k\"]', 'http', :cfg, 'generate_until', 0, 'm', 'completed', '{}', :ts), "
            "('br2', 'p1', 'bench-sim', 'lm_eval', '[\"gsm8k\"]', 'local_sim', '{}', 'generate_until', 0, 'm', 'completed', '{}', :ts)"
        ),
        {
            "cfg": '{"endpoint_url": "https://api.example.com/v1", "api_key": "sk-plaintext"}',
            "ts": now,
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO sandbox_runs (id, project_id, name, runner_backend, solver_config, "
            "scorer_config, sandbox_policy, status, total_tasks, passed_tasks, total_tokens, "
            "total_cost_usd, created_at) "
            "VALUES ('sr1', 'p1', 'sb-1', 'fake', '{}', '{}', '{}', 'completed', 0, 0, 0, 0.0, :ts)"
        ),
        {"ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO safety_scans (id, project_id, status, global_score, total_probes, "
            "passed_count, failed_count, categories_summary, findings, created_at) "
            "VALUES ('sc1', 'p1', 'completed', 0.9, 10, 9, 1, '{}', '[]', :ts)"
        ),
        {"ts": now},
    )


def _insert_0009_edge_rows(conn: sa.Connection) -> None:
    now = utcnow()
    conn.execute(
        sa.text(
            "INSERT INTO sandbox_runs (id, project_id, name, runner_backend, solver_config, "
            "scorer_config, sandbox_policy, status, simulated, total_tasks, passed_tasks, total_tokens, "
            "total_cost_usd, created_at) "
            "VALUES ('sr-unavail', 'p1', 'sb-unavail', 'docker', '{}', '{}', '{}', 'sandbox_unavailable', :sim, "
            "0, 0, 0, 0.0, :ts)"
        ),
        {"sim": False, "ts": now},
    )
    conn.execute(
        sa.text(
            "INSERT INTO safety_scans (id, project_id, status, global_score, simulated, total_probes, "
            "passed_count, failed_count, categories_summary, findings, created_at) "
            "VALUES ('sc-null', 'p1', 'not_evaluated', NULL, :sim, 0, 0, 0, '{}', '[]', :ts)"
        ),
        {"sim": False, "ts": now},
    )


def _assert_0009_data_and_schema(engine: sa.Engine) -> None:
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert {
        "sweeps",
        "sweep_runs",
        "model_stage_history",
        "model_webhooks",
        "webhook_deliveries",
        "gateway_rate_windows",
        "gateway_requests",
    } <= tables

    with engine.connect() as conn:
        cfg, sim_http = conn.execute(
            sa.text("SELECT runner_config, simulated FROM benchmark_runs WHERE id = 'br1'")
        ).one()
        assert "sk-plaintext" not in str(cfg)
        assert "api_key" not in str(cfg)
        assert bool(sim_http) is False

        sim_bench = conn.execute(
            sa.text("SELECT simulated FROM benchmark_runs WHERE id = 'br2'")
        ).scalar_one()
        assert bool(sim_bench) is True

        sim_sb = conn.execute(
            sa.text("SELECT simulated FROM sandbox_runs WHERE id = 'sr1'")
        ).scalar_one()
        assert bool(sim_sb) is True

        stages = dict(
            conn.execute(
                sa.text("SELECT version, stage FROM mlops_model_versions WHERE model_id = 'rm1'")
            ).fetchall()
        )
        assert stages == {1: "archived", 2: "production"}


def test_0009_roundtrip_sqlite(tmp_path):
    """Test 0008 -> 0009 -> 0008 -> 0009 on SQLite including `sandbox_unavailable` downgrade."""
    rev_0009 = _load_rev_0009()
    eng = make_engine(f"sqlite:///{tmp_path / 'mig_0009.db'}")
    try:
        # Start from Base.metadata and downgrade 0009 once to construct the exact 0008 SQLite schema
        Base.metadata.create_all(eng)
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            rev_0009.downgrade()

        with eng.begin() as conn:
            _seed_0008_rows(conn)

        # 0008 -> 0009
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            rev_0009.upgrade()

        _assert_0009_data_and_schema(eng)
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
        assert diff == [], diff

        with eng.begin() as conn:
            _insert_0009_edge_rows(conn)

        # 0009 -> 0008 (must remap 'sandbox_unavailable' to 'failed')
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            rev_0009.downgrade()

        with eng.connect() as conn:
            status = conn.execute(
                sa.text("SELECT status FROM sandbox_runs WHERE id = 'sr-unavail'")
            ).scalar_one()
            assert status == "failed"
            score = conn.execute(
                sa.text("SELECT global_score FROM safety_scans WHERE id = 'sc-null'")
            ).scalar_one()
            assert score == 0.0

        insp = sa.inspect(eng)
        assert "simulated" not in {c["name"] for c in insp.get_columns("sandbox_runs")}
        assert "sweeps" not in set(insp.get_table_names())

        # 0008 -> 0009 again
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            rev_0009.upgrade()

        _assert_0009_data_and_schema(eng)
        with eng.connect() as conn:
            diff2 = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
        assert diff2 == [], diff2
    finally:
        eng.dispose()


def test_0009_alembic_roundtrip_postgres():
    """Test real Alembic `0008 -> 0009 -> 0008 -> 0009` on PostgreSQL with `compare_metadata`."""
    if not PG_URL:
        pytest.skip("MATRIX_TEST_DATABASE_URL not set")

    eng = make_engine(PG_URL)
    cfg = _alembic_config(PG_URL)
    try:
        _drop_all_tables(eng)

        # Upgrade to 0008 and seed representative pre-0009 rows
        command.upgrade(cfg, "0008")
        with eng.begin() as conn:
            _seed_0008_rows(conn)

        # 0008 -> 0009
        command.upgrade(cfg, "0009")
        _assert_0009_data_and_schema(eng)
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        assert diff == [], diff

        # Insert 'sandbox_unavailable' (19 chars) and NULL global_score before downgrade
        with eng.begin() as conn:
            _insert_0009_edge_rows(conn)

        # 0009 -> 0008 must succeed without StringDataRightTruncation
        command.downgrade(cfg, "0008")

        with eng.connect() as conn:
            status = conn.execute(
                sa.text("SELECT status FROM sandbox_runs WHERE id = 'sr-unavail'")
            ).scalar_one()
            assert status == "failed"

        insp = sa.inspect(eng)
        sb_cols = {c["name"]: c for c in insp.get_columns("sandbox_runs")}
        assert "simulated" not in sb_cols
        assert getattr(sb_cols["status"]["type"], "length", None) == 16

        # 0008 -> 0009 again
        command.upgrade(cfg, "0009")
        _assert_0009_data_and_schema(eng)
        with eng.connect() as conn:
            diff2 = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        assert diff2 == [], diff2
    finally:
        _drop_all_tables(eng)
        eng.dispose()
