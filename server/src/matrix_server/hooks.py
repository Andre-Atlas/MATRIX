"""Wiring of the module-level hook lists (`api.ingest.on_spans_ingested`, `api.query.on_score_created`).

Both processes that create scores install the rules score hook: the API (`create_app`) and the
worker (`cli.run_worker`), whose online-eval handler scores spans. Each install first removes what
the previous install put there, so a new app or worker setup (tests, reloads) never stacks hooks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

_installed: list[tuple[list, Callable]] = []


def _install_hook(hooks: list, hook: Callable) -> None:
    hooks.append(hook)
    _installed.append((hooks, hook))


def _uninstall_previous_hooks() -> None:
    while _installed:
        hooks, hook = _installed.pop()
        if hook in hooks:
            hooks.remove(hook)


def install_api_hooks(session_factory: sessionmaker[Session], span_store: Any) -> None:
    """API process: rules + online-eval sampling on ingest, rules on every new score."""
    from matrix_server import online_evals as online_evals_engine
    from matrix_server import rules as rules_engine
    from matrix_server.api import ingest, query

    _uninstall_previous_hooks()
    _install_hook(ingest.on_spans_ingested, rules_engine.make_ingest_hook(session_factory, span_store))
    _install_hook(ingest.on_spans_ingested, online_evals_engine.make_ingest_hook(session_factory))
    _install_hook(query.on_score_created, rules_engine.make_score_hook(span_store))


def install_worker_hooks(span_store: Any) -> None:
    """Worker process: the online-eval handler reads spans from `span_store` and its scores go
    through the rules score hook (annotate / add_to_dataset / alert) in the job's transaction."""
    from matrix_server import rules as rules_engine
    from matrix_server.api import query
    from matrix_server.workers import online_handlers

    _uninstall_previous_hooks()
    online_handlers.set_span_store(span_store)
    _install_hook(query.on_score_created, rules_engine.make_score_hook(span_store))
