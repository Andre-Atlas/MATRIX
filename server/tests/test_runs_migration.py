"""The G1 migration fragment creates exactly the tables of `matrix_server.runs.models`."""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, inspect

from matrix_server.storage.db import Base, make_engine
from matrix_server.storage.models import Project

FRAGMENT = Path(__file__).resolve().parents[1] / "migrations" / "fragments" / "g1_runs.py"
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


def _fragment():
    spec = importlib.util.spec_from_file_location("g1_runs", FRAGMENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    yield eng
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


def test_fragment_matches_models(engine):
    fragment = _fragment()
    Project.__table__.create(engine)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        fragment.upgrade()
    target = MetaData()
    for name in ("projects", *fragment.TABLES):
        Base.metadata.tables[name].to_metadata(target)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), target)
    assert diff == []
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        fragment.downgrade()
    assert set(inspect(engine).get_table_names()) == {"projects"}
