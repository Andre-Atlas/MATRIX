from fastapi import FastAPI

from matrix_server.api import admin, ingest
from matrix_server.config import Settings, get_settings
from matrix_server.storage.db import init_db, make_engine, make_session_factory
from matrix_server.storage.spans import build_span_store


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="MATRIX")
    settings = settings or get_settings()
    app.state.settings = settings

    engine = make_engine(settings.database_url)
    init_db(engine)  # interim until Alembic migrations
    app.state.db_engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.span_store = build_span_store(settings)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(ingest.router)
    app.include_router(admin.router)
    return app
