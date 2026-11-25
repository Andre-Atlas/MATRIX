"""SQLAlchemy engine, session and declarative base for the relational store.

The same models run on SQLite (tests, dev) and PostgreSQL (production), so only
portable column types are used. Production schema is managed by Alembic
(`server/migrations`, `alembic upgrade head`); `init_db` (create_all) remains for tests and dev.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.concurrency import run_in_threadpool


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone-aware columns; treat them as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def _normalize_url(url: str) -> str:
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def make_engine(url: str) -> Engine:
    url = _normalize_url(url)
    if make_url(url).get_backend_name() == "sqlite":
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from matrix_server.storage import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    request.scope.setdefault("_matrix_db_sessions", []).append(session)
    try:
        yield session
        if session.in_transaction():
            session.commit()
    except Exception:
        if session.in_transaction():
            session.rollback()
        raise
    finally:
        session.close()


class CommitBeforeResponseMiddleware:
    """Commits (or rolls back on >=400) request DB sessions BEFORE `http.response.start` is sent.

    FastAPI executes post-`yield` dependency cleanup *after* the response has already been written
    to the socket, creating a read-after-write race on keep-alive / pipelined client requests
    (e.g. `POST /admin/users` -> `201` -> immediate `POST /admin/projects` reading `None` under
    PostgreSQL `READ COMMITTED`). Committing before `http.response.start` eliminates that window
    across all routes and surfaces commit errors before headers go on the wire.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def commit_then_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                sessions: list[Session] = scope.get("_matrix_db_sessions") or []
                status = int(message.get("status", 200))
                for session in sessions:
                    if session.in_transaction():
                        if status < 400:
                            await run_in_threadpool(session.commit)
                        else:
                            await run_in_threadpool(session.rollback)
            await send(message)

        await self.app(scope, receive, commit_then_send)
