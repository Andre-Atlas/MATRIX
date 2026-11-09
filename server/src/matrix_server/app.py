from collections.abc import Callable

from fastapi import FastAPI

from matrix_server import online_evals as online_evals_engine
from matrix_server import rules as rules_engine
from matrix_server.api import (
    admin,
    annotations,
    datasets,
    experiments,
    ingest,
    online_evals,
    prompts,
    query,
    rules,
)
from matrix_server.config import Settings, get_settings
from matrix_server.storage.db import init_db, make_engine, make_session_factory
from matrix_server.storage.spans import build_span_store

# Hook lists live at module level in the API modules. Remember what the last
# create_app installed so a new app (tests, reloads) replaces them instead of stacking.
_installed: list[tuple[list, Callable]] = []


def _install_hook(hooks: list, hook: Callable) -> None:
    hooks.append(hook)
    _installed.append((hooks, hook))


def _uninstall_previous_hooks() -> None:
    while _installed:
        hooks, hook = _installed.pop()
        if hook in hooks:
            hooks.remove(hook)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="MATRIX")
    settings = settings or get_settings()
    app.state.settings = settings

    engine = make_engine(settings.database_url)
    init_db(engine)  # dev/tests; production runs `alembic upgrade head` first (create_all is then a no-op)
    app.state.db_engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.span_store = build_span_store(settings)

    _uninstall_previous_hooks()
    _install_hook(ingest.on_spans_ingested, rules_engine.make_ingest_hook(app.state.session_factory, app.state.span_store))
    _install_hook(ingest.on_spans_ingested, online_evals_engine.make_ingest_hook(app.state.session_factory))
    _install_hook(query.on_score_created, rules_engine.make_score_hook(app.state.span_store))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for module in (ingest, admin, query, prompts, datasets, experiments, annotations, rules, online_evals):
        app.include_router(module.router)
    return app
