"""Worker handlers for CI/CD registry webhooks (`webhooks.deliver`)."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from matrix_server.mlops.webhooks import (
    WEBHOOK_DELIVER_JOB,
    deliver_webhook_job,
    mark_delivery_permanently_failed,
)
from matrix_server.storage.models import Job
from matrix_server.workers.runner import on_final_failure, register

_secret_key_override: str | None = None


def set_secret_key(key: str | None) -> None:
    """Override MATRIX_SECRET_KEY for worker tests; `None` restores env/settings lookup."""
    global _secret_key_override
    _secret_key_override = key


@register(WEBHOOK_DELIVER_JOB)
def handle_webhook_deliver(session: Session, job: Job) -> None:
    deliver_webhook_job(session, job, secret_key=_secret_key_override)


@on_final_failure(WEBHOOK_DELIVER_JOB)
def settle_webhook_delivery(session_factory: sessionmaker[Session], job: Job) -> None:
    mark_delivery_permanently_failed(session_factory, job)
