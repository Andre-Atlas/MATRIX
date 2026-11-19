"""CI/CD Webhook encryption, signing, dispatching, and delivery via egress_client (T6, ADR-012)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.config import get_settings
from matrix_server.egress import EgressError, egress_client
from matrix_server.mlops.registry_models import ModelWebhook, WebhookDelivery
from matrix_server.storage.db import utcnow
from matrix_server.storage.jobs import enqueue
from matrix_server.storage.models import Job

log = logging.getLogger(__name__)

WEBHOOK_DELIVER_JOB = "webhooks.deliver"
WEBHOOK_MAX_ATTEMPTS = 5
VALID_WEBHOOK_EVENTS = frozenset({"version.created", "stage.changed", "alias.moved"})
# Only transient transport/DNS errors from egress_client should be retried; all policy rejections
# (host_not_allowed, ip_literal_not_allowed, private/link_local/loopback IPs, too_many_redirects, etc.)
# fail the delivery cleanly on the first attempt.
_RETRYABLE_EGRESS_REASONS = frozenset({"timeout", "connection_error", "dns_failure"})


class SecretKeyMissingError(RuntimeError):
    """Raised when MATRIX_SECRET_KEY is not configured."""


class WebhookValidationError(ValueError):
    """Raised when a webhook URL or event list is invalid."""


class WebhookDeliveryError(RuntimeError):
    """Raised on transient delivery failure so the worker retries with exponential backoff.

    Never includes the webhook secret or response body.
    """


def resolve_secret_key(explicit: str | None = None) -> str:
    """Resolve MATRIX_SECRET_KEY from explicit value, env var, or settings; raise if unset."""
    key = explicit
    if not key:
        key = os.environ.get("MATRIX_SECRET_KEY")
    if not key:
        key = get_settings().secret_key
    if not key or not key.strip():
        raise SecretKeyMissingError("MATRIX_SECRET_KEY must be configured to use registry webhooks")
    return key.strip()


def _fernet(secret_key: str) -> Fernet:
    """Derive a valid 32-byte URL-safe base64 Fernet key from MATRIX_SECRET_KEY via SHA-256."""
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, secret_key: str) -> str:
    try:
        return _fernet(secret_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise WebhookDeliveryError("failed to decrypt webhook secret") from exc


def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def sign_payload(secret: str, timestamp: str, body_bytes: bytes) -> str:
    """Compute `sha256=<hmac(secret, timestamp + '.' + body)>`."""
    signed_input = f"{timestamp}.".encode() + body_bytes
    mac = hmac.new(secret.encode("utf-8"), signed_input, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def validate_webhook_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2000:
        raise WebhookValidationError("webhook url must be 1..2000 characters")
    parsed = urlsplit(cleaned)
    if parsed.scheme.lower() not in ("http", "https"):
        raise WebhookValidationError("webhook url must use http or https")
    if not parsed.hostname:
        raise WebhookValidationError("webhook url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookValidationError("webhook url must not contain embedded credentials")
    return cleaned


def validate_webhook_events(events: list[str]) -> list[str]:
    if not events:
        raise WebhookValidationError("events must be a non-empty list")
    cleaned: list[str] = []
    for ev in events:
        if ev not in VALID_WEBHOOK_EVENTS:
            raise WebhookValidationError(f"unsupported webhook event: {ev!r}")
        if ev not in cleaned:
            cleaned.append(ev)
    return cleaned


def dispatch_model_event(
    db: Session,
    *,
    project_id: str,
    model_id: str,
    event: str,
    payload: dict[str, Any],
) -> list[WebhookDelivery]:
    """Create WebhookDelivery rows and enqueue `webhooks.deliver` jobs for matching active webhooks."""
    webhooks = list(
        db.scalars(
            select(ModelWebhook).where(
                ModelWebhook.project_id == project_id,
                ModelWebhook.model_id == model_id,
                ModelWebhook.active.is_(True),
            )
        )
    )
    deliveries: list[WebhookDelivery] = []
    for wh in webhooks:
        if event not in (wh.events or []):
            continue
        delivery = WebhookDelivery(
            project_id=project_id,
            webhook_id=wh.id,
            model_id=model_id,
            event=event,
            payload=payload,
            status="queued",
            attempts=0,
        )
        db.add(delivery)
        db.flush()
        job = enqueue(
            db,
            project_id,
            WEBHOOK_DELIVER_JOB,
            {"delivery_id": delivery.id, "webhook_id": wh.id},
            max_attempts=WEBHOOK_MAX_ATTEMPTS,
        )
        delivery.job_id = job.id
        deliveries.append(delivery)
    if deliveries:
        db.flush()
    return deliveries


def _update_delivery_state(
    session_factory: sessionmaker[Session],
    delivery_id: str,
    *,
    status: str,
    attempts: int,
    last_status_code: int | None,
    last_error: str | None,
    delivered: bool = False,
) -> None:
    """Persist delivery state in an independent transaction so worker rollbacks don't erase attempts."""
    with session_factory() as s, s.begin():
        row = s.get(WebhookDelivery, delivery_id)
        if row is None:
            return
        now = utcnow()
        row.status = status
        row.attempts = attempts
        row.last_status_code = last_status_code
        row.last_error = last_error[:500] if last_error else None
        row.last_attempt_at = now
        if delivered:
            row.delivered_at = now


def deliver_webhook_job(
    session: Session,
    job: Job,
    *,
    secret_key: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Execute one `webhooks.deliver` job attempt through `egress_client(purpose='webhook')`."""
    payload_data = job.payload or {}
    delivery_id = payload_data.get("delivery_id")
    if not delivery_id:
        return

    delivery = session.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.project_id == job.project_id,
        )
    )
    if delivery is None:
        return
    webhook = session.scalar(
        select(ModelWebhook).where(
            ModelWebhook.id == delivery.webhook_id,
            ModelWebhook.project_id == job.project_id,
        )
    )
    if webhook is None or not webhook.active:
        return

    factory = session_factory or sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    attempt_num = max(int(delivery.attempts or 0) + 1, int(job.attempts or 1))
    is_last_attempt = int(job.attempts or 1) >= int(job.max_attempts or WEBHOOK_MAX_ATTEMPTS)

    try:
        key = resolve_secret_key(secret_key)
        secret = decrypt_secret(webhook.secret_encrypted, key)
    except (SecretKeyMissingError, WebhookDeliveryError) as exc:
        _update_delivery_state(
            factory,
            delivery.id,
            status="failed",
            attempts=attempt_num,
            last_status_code=None,
            last_error=f"crypto:{type(exc).__name__}",
        )
        log.error("webhook delivery %s failed: encryption key unavailable", delivery.id)
        return

    body_bytes = json.dumps(delivery.payload or {}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = sign_payload(secret, timestamp, body_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-Matrix-Signature": signature,
        "X-Matrix-Timestamp": timestamp,
        "X-Matrix-Event": delivery.event,
        "X-Matrix-Delivery": delivery.id,
    }

    client = egress_client(
        project_id=delivery.project_id,
        session_factory=factory,
        purpose="webhook",
        timeout=10.0,
    )

    try:
        resp = client.post(webhook.url, content=body_bytes, headers=headers)
    except EgressError as exc:
        reason = f"egress:{exc.reason}"
        if exc.reason not in _RETRYABLE_EGRESS_REASONS:
            _update_delivery_state(
                factory,
                delivery.id,
                status="failed",
                attempts=attempt_num,
                last_status_code=None,
                last_error=reason,
            )
            log.warning("webhook delivery %s blocked by egress policy (%s)", delivery.id, exc.reason)
            return
        _update_delivery_state(
            factory,
            delivery.id,
            status="failed" if is_last_attempt else "queued",
            attempts=attempt_num,
            last_status_code=None,
            last_error=reason,
        )
        raise WebhookDeliveryError(f"webhook delivery failed ({reason})") from None

    if 200 <= resp.status_code < 300:
        _update_delivery_state(
            factory,
            delivery.id,
            status="delivered",
            attempts=attempt_num,
            last_status_code=resp.status_code,
            last_error=None,
            delivered=True,
        )
        return

    # Never log or store response body
    err_msg = f"http:{resp.status_code}"
    _update_delivery_state(
        factory,
        delivery.id,
        status="failed" if is_last_attempt else "queued",
        attempts=attempt_num,
        last_status_code=resp.status_code,
        last_error=err_msg,
    )
    raise WebhookDeliveryError(f"webhook receiver returned HTTP {resp.status_code}")


def mark_delivery_permanently_failed(session_factory: sessionmaker[Session], job: Job) -> None:
    delivery_id = (job.payload or {}).get("delivery_id")
    if not delivery_id:
        return
    with session_factory() as s, s.begin():
        row = s.get(WebhookDelivery, delivery_id)
        if row is not None and row.status != "delivered":
            row.status = "failed"
            if not row.last_error:
                row.last_error = "max_attempts_exceeded"
