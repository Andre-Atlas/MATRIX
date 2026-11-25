"""The T8 migration fragment matches `gateway.models` and T8 `safety_scans` schema."""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, inspect

import matrix_server.gateway
import matrix_server.storage.models  # noqa: F401 - register SafetyScan on Base.metadata
from matrix_server.storage.db import Base, make_engine

FRAGMENT = (
    Path(__file__).resolve().parents[1] / "migrations" / "fragments" / "t8_gateway_safety_vectors.py"
)
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


def _fragment():
    spec = importlib.util.spec_from_file_location("t8_gateway_safety_vectors", FRAGMENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_0008_safety_scans(engine) -> None:
    """Recreates the pre-T8 (0008) `safety_scans` table before applying the fragment."""
    md = MetaData()
    sa.Table(
        "safety_scans",
        md,
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("target_prompt", sa.Text(), nullable=True),
        sa.Column("target_model", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("global_score", sa.Float(), nullable=False),
        sa.Column("total_probes", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("categories_summary", sa.JSON(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    md.create_all(engine)


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 't8_mig.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    yield eng
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


def test_t8_fragment_matches_models(engine):
    fragment = _fragment()
    _create_0008_safety_scans(engine)

    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        fragment.upgrade()

    target = MetaData()
    for name in ("safety_scans", *fragment.TABLES):
        Base.metadata.tables[name].to_metadata(target)

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), target)
    assert diff == []

    # Verify nullable global_score works and downgrade cleanly restores 0008 schema
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO safety_scans "
                "(id, project_id, status, global_score, simulated, total_probes, "
                "passed_count, failed_count, categories_summary, findings, created_at) "
                "VALUES ('s1', 'p1', 'not_evaluated', NULL, FALSE, 0, 0, 0, '{}', '[]', '2026-11-19T00:00:00Z')"
            )
        )

    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        fragment.downgrade()

    insp = inspect(engine)
    assert set(insp.get_table_names()) == {"safety_scans"}
    cols = {c["name"]: c for c in insp.get_columns("safety_scans")}
    assert "simulated" not in cols
    assert cols["global_score"]["nullable"] is False
