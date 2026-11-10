"""Users, passwords, web sessions and login throttling (ADR-006, contract G0 section 1).

- Passwords: argon2id via `argon2.PasswordHasher` (RFC 9106 low-memory profile: t=3, m=64 MiB,
  p=4). Hashes are upgraded on login when the parameters change (`check_needs_rehash`). Unknown
  users are verified against a fixed dummy hash so both failure paths cost one argon2 verification.
- Sessions: opaque `mxs_<token_urlsafe(32)>`; only its SHA-256 is stored (`WebSession.id`). A
  session expires after `session_idle_ttl_seconds` without use (sliding; `last_seen_at` is written
  at most once per minute) and `session_absolute_ttl_seconds` after creation.
- Throttling: failures within `FAILURE_WINDOW` are counted under three keys (`throttle_keys`):
  the (e-mail, IP) pair (`PAIR_MAX_FAILURES`), the IP alone (`IP_MAX_FAILURES`, stops one client
  spraying many e-mails) and the e-mail alone (`EMAIL_MAX_FAILURES`, stops a distributed attack on
  one account). Reaching a limit locks that key for `LOCK_STEPS[n]` (escalating). A successful
  login clears the pair and e-mail keys, never the IP key (an attacker could reset it with their
  own account). Keys are hashes, so a locked unknown e-mail behaves exactly like a known one.
- Cost bound: at most `hash_concurrency` argon2 operations run at once in the process (each uses
  64 MiB); a caller that waits longer than `HASH_WAIT_SECONDS` gets `PasswordHasherBusy` (503).

Nothing here logs passwords, tokens or full e-mails.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.storage.db import as_utc, utcnow
from matrix_server.storage.models import LoginAttempt, User, WebSession

PASSWORD_MIN_CHARS = 12
PASSWORD_MAX_CHARS = 256
EMAIL_MAX_CHARS = 320
NAME_MAX_CHARS = 200

SESSION_SCHEME = "mxs"
SESSION_TOKEN_BYTES = 32
LAST_SEEN_RESOLUTION = timedelta(minutes=1)

PAIR_MAX_FAILURES = 5
IP_MAX_FAILURES = 30
EMAIL_MAX_FAILURES = 20
MAX_FAILURES = PAIR_MAX_FAILURES
FAILURE_WINDOW = timedelta(minutes=15)
LOCK_STEPS = (timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=15))
# Lock escalation is forgotten after this long without a new lock.
LOCK_MEMORY = timedelta(hours=24)

password_hasher = PasswordHasher()
_dummy_hash: str | None = None

DEFAULT_HASH_CONCURRENCY = 4
HASH_WAIT_SECONDS = 10.0
_hash_slots = threading.BoundedSemaphore(DEFAULT_HASH_CONCURRENCY)


class PasswordHasherBusy(RuntimeError):
    """Too many password hashes in flight; the caller answers 503 with Retry-After."""


def configure_hash_concurrency(limit: int) -> None:
    global _hash_slots
    _hash_slots = threading.BoundedSemaphore(max(1, int(limit)))


@contextmanager
def _hash_slot() -> Iterator[None]:
    slots = _hash_slots
    if not slots.acquire(timeout=HASH_WAIT_SECONDS):
        raise PasswordHasherBusy("password hashing is saturated")
    try:
        yield
    finally:
        slots.release()


class UserError(ValueError):
    """Invalid user input; the message is safe to return to the client (never echoes secrets)."""


class UserExistsError(UserError):
    pass


# --- e-mail and password rules -------------------------------------------------


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    """Return the normalized e-mail or raise UserError."""
    value = normalize_email(email)
    if not 3 <= len(value) <= EMAIL_MAX_CHARS:
        raise UserError("invalid email")
    local, sep, domain = value.partition("@")
    if not sep or not local or not domain or "@" in domain or "." not in domain.strip("."):
        raise UserError("invalid email")
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value):
        raise UserError("invalid email")
    return value


def validate_password(password: str, email: str | None = None) -> None:
    if not isinstance(password, str):
        raise UserError("password must be a string")
    if len(password) < PASSWORD_MIN_CHARS:
        raise UserError(f"password must have at least {PASSWORD_MIN_CHARS} characters")
    if len(password) > PASSWORD_MAX_CHARS:
        raise UserError(f"password must have at most {PASSWORD_MAX_CHARS} characters")
    if email is not None and normalize_email(password) == normalize_email(email):
        raise UserError("password must not be the email")


def hash_password(password: str) -> str:
    with _hash_slot():
        return password_hasher.hash(password)


def dummy_hash() -> str:
    """A fixed hash (per process) with the current parameters, verified for unknown users."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(24))
    return _dummy_hash


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Always runs exactly one argon2 verification (against the dummy hash when `stored_hash` is
    None), so an unknown user costs the same as a wrong password."""
    target = stored_hash if stored_hash else dummy_hash()
    with _hash_slot():
        try:
            password_hasher.verify(target, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return bool(stored_hash)


def mask_email(email: str) -> str:
    """`a***@example.com`, for logs and audit details."""
    local, sep, domain = normalize_email(email).partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain[:100]}"


# --- users -------------------------------------------------------------------


def get_user_by_email(session: Session, email: str) -> User | None:
    value = normalize_email(email)
    if not value or len(value) > EMAIL_MAX_CHARS:
        return None
    return session.scalar(select(User).where(User.email == value))


def create_user(session: Session, email: str, name: str, password: str) -> User:
    value = validate_email(email)
    name = (name or "").strip()
    if len(name) > NAME_MAX_CHARS:
        raise UserError(f"name must have at most {NAME_MAX_CHARS} characters")
    validate_password(password, value)
    if session.scalar(select(User.id).where(User.email == value)) is not None:
        raise UserExistsError("a user with this email already exists")
    user = User(email=value, name=name, password_hash=hash_password(password))
    try:
        with session.begin_nested():
            session.add(user)
    except IntegrityError:  # concurrent creation of the same e-mail
        raise UserExistsError("a user with this email already exists") from None
    return user


def set_password(session: Session, user: User, password: str) -> None:
    validate_password(password, user.email)
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    session.flush()


def rehash_if_needed(user: User, password: str) -> bool:
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        return True
    return False


# --- sessions ----------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    return f"{SESSION_SCHEME}_{secrets.token_urlsafe(SESSION_TOKEN_BYTES)}"


def is_session_token(raw: str) -> bool:
    return raw.startswith(f"{SESSION_SCHEME}_")


def create_session(
    session: Session,
    user: User,
    *,
    absolute_ttl: timedelta,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, WebSession]:
    token = generate_session_token()
    now = utcnow()
    row = WebSession(
        id=hash_token(token),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + absolute_ttl,
        user_agent=(user_agent or "")[:300] or None,
        ip=(ip or "")[:64] or None,
    )
    session.add(row)
    session.flush()
    return token, row


def session_is_active(row: WebSession, now: datetime, idle_ttl: timedelta) -> bool:
    if row.revoked_at is not None:
        return False
    if now >= as_utc(row.expires_at):
        return False
    return now < as_utc(row.last_seen_at) + idle_ttl


def touch_session(row: WebSession, now: datetime) -> bool:
    """Slide the idle expiry; writes at most once per LAST_SEEN_RESOLUTION."""
    if now - as_utc(row.last_seen_at) >= LAST_SEEN_RESOLUTION:
        row.last_seen_at = now
        return True
    return False


def revoke_user_sessions(session: Session, user_id: str, *, except_id: str | None = None) -> int:
    stmt = update(WebSession).where(WebSession.user_id == user_id, WebSession.revoked_at.is_(None))
    if except_id is not None:
        stmt = stmt.where(WebSession.id != except_id)
    result = session.execute(stmt.values(revoked_at=utcnow()).execution_options(synchronize_session=False))
    return result.rowcount or 0


def active_sessions(session: Session, user_id: str, idle_ttl: timedelta) -> list[WebSession]:
    now = utcnow()
    rows = session.scalars(
        select(WebSession)
        .where(WebSession.user_id == user_id, WebSession.revoked_at.is_(None))
        .order_by(WebSession.created_at.desc(), WebSession.id)
    ).all()
    return [r for r in rows if session_is_active(r, now, idle_ttl)]


# --- login throttling -----------------------------------------------------------


def _throttle_hash(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def attempt_key(email: str, ip: str | None) -> str:
    """Key of the (e-mail, IP) pair."""
    return _throttle_hash(normalize_email(email), ip or "")


def ip_key(ip: str | None) -> str:
    return _throttle_hash("ip", ip or "")


def email_key(email: str) -> str:
    return _throttle_hash("email", normalize_email(email))


def throttle_keys(email: str, ip: str | None) -> list[tuple[str, int]]:
    """(key, max failures) for every counter a login failure feeds."""
    return [
        (attempt_key(email, ip), PAIR_MAX_FAILURES),
        (ip_key(ip), IP_MAX_FAILURES),
        (email_key(email), EMAIL_MAX_FAILURES),
    ]


def locked_for_any(session: Session, keys: list[tuple[str, int]], now: datetime | None = None) -> int | None:
    """Longest remaining lock among `keys` (seconds), or None."""
    now = now or utcnow()
    waits = [w for key, _ in keys if (w := locked_for(session, key, now)) is not None]
    return max(waits) if waits else None


def register_failures(session: Session, keys: list[tuple[str, int]], now: datetime | None = None) -> int | None:
    """Count one failure under every key; the longest lock it starts (seconds), or None."""
    now = now or utcnow()
    locks = [lk for key, limit in keys if (lk := register_failure(session, key, now, limit)) is not None]
    return max(locks) if locks else None


def clear_login_failures(session: Session, email: str, ip: str | None) -> None:
    """After a successful login: the pair and e-mail counters reset, the IP counter does not."""
    clear_failures(session, attempt_key(email, ip))
    clear_failures(session, email_key(email))


def locked_for(session: Session, key: str, now: datetime | None = None) -> int | None:
    """Seconds until the (e-mail, IP) pair may try again, or None when it is not locked."""
    now = now or utcnow()
    row = session.get(LoginAttempt, key)
    if row is None or row.locked_until is None:
        return None
    remaining = (as_utc(row.locked_until) - now).total_seconds()
    return max(1, math.ceil(remaining)) if remaining > 0 else None


def _attempt_row(session: Session, key: str, now: datetime) -> LoginAttempt:
    row = session.get(LoginAttempt, key, with_for_update=True)
    if row is not None:
        return row
    try:
        with session.begin_nested():
            row = LoginAttempt(key=key, window_start=now, count=0, lock_count=0)
            session.add(row)
    except IntegrityError:  # concurrent first failure for the same pair
        row = session.get(LoginAttempt, key, with_for_update=True, populate_existing=True)
        assert row is not None
    return row


def register_failure(
    session: Session, key: str, now: datetime | None = None, max_failures: int = PAIR_MAX_FAILURES
) -> int | None:
    """Count one failure; return the lock duration in seconds when this failure locks the pair."""
    now = now or utcnow()
    row = _attempt_row(session, key, now)
    # `locked_until` stays set after the lock expires: it dates the last lock for LOCK_MEMORY.
    locked_until = as_utc(row.locked_until)
    if locked_until is not None and now - locked_until >= LOCK_MEMORY:
        row.lock_count = 0
    if now - as_utc(row.window_start) > FAILURE_WINDOW:
        row.window_start = now
        row.count = 0
    row.count += 1
    lock_seconds = None
    if row.count >= max_failures:
        step = LOCK_STEPS[min(row.lock_count, len(LOCK_STEPS) - 1)]
        row.lock_count += 1
        row.locked_until = now + step
        row.count = 0
        row.window_start = now
        lock_seconds = int(step.total_seconds())
    session.flush()
    return lock_seconds


def clear_failures(session: Session, key: str) -> None:
    row = session.get(LoginAttempt, key)
    if row is not None:
        session.delete(row)
        session.flush()
