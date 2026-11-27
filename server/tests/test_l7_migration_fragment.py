"""The L7 migration fragment (`l7_data_sets.py`) applied over the current schema (head 0009)."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from matrix_server.storage import models  # noqa: F401 - register every table on Base.metadata
from matrix_server.storage.db import Base, make_engine

SERVER_DIR = Path(__file__).resolve().parents[1]
FRAGMENT = SERVER_DIR / "migrations" / "fragments" / "l7_data_sets.py"
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


def _fragment():
    spec = importlib.util.spec_from_file_location("l7_data_sets", FRAGMENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_schema(engine: sa.Engine, url: str, fragment) -> None:
    """The schema at head 0009: `alembic upgrade head` on PostgreSQL (the migrations are not
    SQLite-compatible), every other table of the models on SQLite."""
    if engine.dialect.name == "postgresql":
        _alembic_head(url)
        return
    tables = [t for t in Base.metadata.sorted_tables if t.name not in fragment.TABLES]
    Base.metadata.create_all(engine, tables=tables)


def _alembic_head(url: str) -> None:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def _apply(engine: sa.Engine, fn) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        fn()


def _l7_diffs(engine: sa.Engine, tables: tuple[str, ...]) -> list:
    def include(obj, name, type_, reflected, compare_to) -> bool:
        table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
        return table in tables

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_object": include, "compare_type": True})
        return compare_metadata(ctx, Base.metadata)


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path}/l7.db")
        url = f"sqlite:///{tmp_path}/l7.db"
        yield eng, url
        eng.dispose()
        return
    if not PG_URL:
        pytest.skip("MATRIX_TEST_DATABASE_URL not set")
    schema = f"l7m_{uuid.uuid4().hex[:10]}"  # private schema: parallel runs share the database
    admin = sa.create_engine(PG_URL)
    with admin.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    sep = "&" if "?" in PG_URL else "?"
    url = f"{PG_URL}{sep}options=-csearch_path%3D{schema}"
    eng = make_engine(url)
    yield eng, url
    eng.dispose()
    with admin.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


def test_fragment_over_head_0009_matches_models_and_round_trips(engine):
    eng, url = engine
    fragment = _fragment()
    _current_schema(eng, url, fragment)  # revision 0009
    tables = set(sa.inspect(eng).get_table_names())
    assert "projects" in tables and "runs" in tables and not tables & set(fragment.TABLES)

    _apply(eng, fragment.upgrade)
    insp = sa.inspect(eng)
    assert set(fragment.TABLES) <= set(insp.get_table_names())
    assert _l7_diffs(eng, fragment.TABLES) == []  # same schema as the SQLAlchemy models

    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO data_sets (id, project_id, name, kind, created_at) "
            "VALUES ('d1', 'p1', 'corpus', 'files', CURRENT_TIMESTAMP)"))
        conn.execute(sa.text(
            "INSERT INTO data_set_versions (id, project_id, data_set_id, version, manifest_sha256, file_count, "
            "total_bytes, source_type, created_at) VALUES ('v1', 'p1', 'd1', 1, :h, 1, 3, 'upload', CURRENT_TIMESTAMP)"),
            {"h": "0" * 64})
        conn.execute(sa.text(
            "INSERT INTO data_set_files (version_id, path, project_id, sha256, size) "
            "VALUES ('v1', 'a/b.txt', 'p1', :h, 3)"), {"h": "1" * 64})
        conn.execute(sa.text(
            "INSERT INTO data_set_tags (data_set_id, tag, project_id, version, updated_at) "
            "VALUES ('d1', 'prod', 'p1', 1, CURRENT_TIMESTAMP)"))
    with eng.begin() as conn, pytest.raises(sa.exc.IntegrityError):  # constraints are in place
        conn.execute(sa.text(
            "INSERT INTO data_sets (id, project_id, name, kind, created_at) "
            "VALUES ('d2', 'p1', 'corpus', 'files', CURRENT_TIMESTAMP)"))
    with eng.begin() as conn:  # deleting the data-set cascades to versions, files and tags
        if eng.dialect.name == "sqlite":
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        conn.execute(sa.text("DELETE FROM data_sets WHERE id = 'd1'"))
        for table in ("data_set_versions", "data_set_files", "data_set_tags"):
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0

    _apply(eng, fragment.downgrade)
    assert not set(sa.inspect(eng).get_table_names()) & set(fragment.TABLES)
    _apply(eng, fragment.upgrade)  # re-applicable after a downgrade
    assert _l7_diffs(eng, fragment.TABLES) == []
    _apply(eng, fragment.downgrade)
