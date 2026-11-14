from fastapi import FastAPI

from matrix_server.api import (
    admin,
    annotations,
    auth,
    benchmarks,
    datasets,
    experiments,
    gateway,
    ingest,
    mlops,
    online_evals,
    prompts,
    query,
    rules,
    safety,
    sandboxes,
    vectors,
)
from matrix_server.config import Settings, get_settings
from matrix_server.gateway.router import GatewayRouter
from matrix_server.hooks import install_api_hooks
from matrix_server.limits import BodyLimitMiddleware
from matrix_server.mlops.artifact_store import build_artifact_store
from matrix_server.mlops.metrics_store import build_training_metric_store
from matrix_server.storage.db import init_db, make_engine, make_session_factory
from matrix_server.storage.spans import build_span_store


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="MATRIX")
    settings = settings or get_settings()
    app.state.settings = settings
    app.add_middleware(
        BodyLimitMiddleware,
        max_body_bytes=settings.max_body_bytes,
        max_json_body_bytes=settings.max_json_body_bytes,
        max_artifact_bytes=settings.max_artifact_bytes,
    )

    engine = make_engine(settings.database_url)
    init_db(engine)  # dev/tests; production runs `alembic upgrade head` first (create_all is then a no-op)
    app.state.db_engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.span_store = build_span_store(settings)
    app.state.training_metric_store = build_training_metric_store(settings)
    app.state.artifact_store = build_artifact_store(settings)
    app.state.gateway_router = GatewayRouter()

    install_api_hooks(app.state.session_factory, app.state.span_store)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for module in (
        auth,
        ingest,
        admin,
        query,
        prompts,
        datasets,
        experiments,
        annotations,
        rules,
        safety,
        online_evals,
        vectors,
        gateway,
        mlops,
        benchmarks,
        sandboxes,
    ):
        app.include_router(module.router)
    return app
