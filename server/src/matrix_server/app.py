from fastapi import FastAPI

from matrix_server import users
from matrix_server.api import (
    admin,
    annotations,
    artifacts,
    auth,
    datasets,
    egress,
    experiments,
    ingest,
    online_evals,
    projects,
    prompts,
    query,
    rules,
    runs,
)
from matrix_server.config import Settings, get_settings
from matrix_server.hooks import install_api_hooks
from matrix_server.limits import BodyLimitMiddleware
from matrix_server.runs.store import build_metric_store
from matrix_server.storage.db import init_db, make_engine, make_session_factory
from matrix_server.storage.spans import build_span_store


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="MATRIX")
    settings = settings or get_settings()
    app.state.settings = settings
    users.configure_hash_concurrency(settings.password_hash_concurrency)
    app.add_middleware(
        BodyLimitMiddleware,
        max_body_bytes=settings.max_body_bytes,
        max_json_body_bytes=settings.max_json_body_bytes,
        raw_upload_limit=settings.max_artifact_bytes,
    )

    engine = make_engine(settings.database_url)
    init_db(engine)  # dev/tests; production runs `alembic upgrade head` first (create_all is then a no-op)
    app.state.db_engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.span_store = build_span_store(settings)
    app.state.metric_store = build_metric_store(settings)

    install_api_hooks(app.state.session_factory, app.state.span_store)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for module in (ingest, admin, query, prompts, datasets, experiments, annotations, rules, online_evals,
                   egress, artifacts, auth, projects, runs):
        app.include_router(module.router)
    return app
