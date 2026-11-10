"""Run domain logic shared by the API and the worker (contract G1, ADR-008).

- `parse_points`: per-point validation of a metrics batch (partial acceptance).
- `apply_point_caps`: per-run / per-key / distinct-key caps against the run's write counters.
- `merge_params`: immutable params (equal value is a no-op, a different one is a conflict).
- `artifact_exists` / `load_artifacts`: the only code that reads the G0 `artifacts` table.
- `reap_stale_runs` and `ensure_reaper_scheduled`: the `runs.reaper` job and its self-rescheduling.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    and_,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.orm import Session

from matrix_server.runs.models import Run
from matrix_server.runs.store import MetricPoint
from matrix_server.storage import jobs
from matrix_server.storage.db import as_utc, utcnow
from matrix_server.storage.models import Job

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_./:@ -]{1,200}$")
MAX_POINTS_PER_REQUEST = 10_000
MAX_KEYS_PER_RUN = 1000
# Steps stay exact in JavaScript numbers (and keep M4 bucket arithmetic within UInt64).
MAX_STEP = 2**53 - 1
MIN_TS = datetime(1970, 1, 1, tzinfo=UTC)
MAX_TS = datetime(2200, 1, 1, tzinfo=UTC)
NON_FINITE = {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}

CLOSED_GRACE = timedelta(minutes=10)
HEARTBEAT_TIMEOUT = timedelta(minutes=5)
LOST_HEARTBEAT = "lost heartbeat"

REAPER_KIND = "runs.reaper"
PURGE_KIND = "runs.purge_metrics"
REAPER_INTERVAL = timedelta(minutes=1)
# System jobs are not tied to a project; this id never matches a real one (ids are 32 hex chars).
SYSTEM_PROJECT_ID = "_system"
_REAPER_LOCK_ID = 0x6D78_7275_6E73  # pg_advisory_xact_lock key ("mxruns")


# --------------------------------------------------------------------------- metric points


@dataclass(frozen=True, slots=True)
class Rejected:
    index: int
    reason: str


def _parse_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        try:
            return float(value)
        except OverflowError:
            return None
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return NON_FINITE.get(value)
    return None


def _parse_ts(value: Any, now: datetime) -> datetime | None:
    if value is None:
        return now
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    if not (MIN_TS <= ts < MAX_TS):
        return None
    return ts


def to_micros(value: datetime) -> int:
    value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta = value - MIN_TS
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_micros(micros: int) -> datetime:
    return MIN_TS + timedelta(microseconds=int(micros))


def parse_points(raw: Sequence[Any], now: datetime) -> tuple[list[tuple[int, MetricPoint]], list[Rejected]]:
    """Validate each point; returns ((index, point) accepted, rejected). Never raises per point."""
    accepted: list[tuple[int, MetricPoint]] = []
    rejected: list[Rejected] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            rejected.append(Rejected(index, "point must be an object"))
            continue
        unknown = set(item) - {"key", "step", "value", "ts"}
        if unknown:
            rejected.append(Rejected(index, "unknown field"))
            continue
        key = item.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            rejected.append(Rejected(index, "invalid key"))
            continue
        step = item.get("step")
        if isinstance(step, bool) or not isinstance(step, int) or step < 0 or step > MAX_STEP:
            rejected.append(Rejected(index, "step must be an integer between 0 and 2**53-1"))
            continue
        value = _parse_value(item.get("value"))
        if value is None:
            rejected.append(Rejected(index, 'value must be a number or "nan", "inf", "-inf"'))
            continue
        ts = _parse_ts(item.get("ts"), now)
        if ts is None:
            rejected.append(Rejected(index, "ts must be an ISO 8601 datetime between 1970 and 2200"))
            continue
        accepted.append((index, MetricPoint(key=key, step=step, value=value, ts_us=to_micros(ts))))
    return accepted, rejected


def apply_point_caps(
    run: Run, points: list[tuple[int, MetricPoint]], *, max_per_run: int, max_per_key: int
) -> tuple[list[MetricPoint], list[Rejected]]:
    """Keep the points that fit the run's caps and bump its counters (the caller commits)."""
    counts = dict(run.key_counts or {})
    total = int(run.point_count or 0)
    kept: list[MetricPoint] = []
    rejected: list[Rejected] = []
    for index, point in points:
        current = counts.get(point.key)
        if current is None and len(counts) >= MAX_KEYS_PER_RUN:
            rejected.append(Rejected(index, f"run has the maximum of {MAX_KEYS_PER_RUN} keys"))
            continue
        if total >= max_per_run:
            rejected.append(Rejected(index, f"run reached its limit of {max_per_run} points"))
            continue
        if (current or 0) >= max_per_key:
            rejected.append(Rejected(index, f"key reached its limit of {max_per_key} points"))
            continue
        counts[point.key] = (current or 0) + 1
        total += 1
        kept.append(point)
    if kept:
        run.key_counts = counts  # new dict: JSON columns only track reassignment
        run.point_count = total
    return kept, rejected


def accepts_points(run: Run, now: datetime) -> bool:
    """Running runs accept points; closed ones for `CLOSED_GRACE` after `ended_at`."""
    if run.status == "running":
        return True
    ended = as_utc(run.ended_at)
    return ended is not None and now - ended <= CLOSED_GRACE


# --------------------------------------------------------------------------- params


def canonical(value: Any) -> str:
    """Type-preserving comparison form (1, 1.0 and true differ)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def merge_params(existing: dict[str, Any], new: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merged params and the keys whose new value differs from the stored one."""
    conflicts = sorted(k for k, v in new.items() if k in existing and canonical(existing[k]) != canonical(v))
    if conflicts:
        return existing, conflicts
    return {**existing, **new}, []


# --------------------------------------------------------------------------- artifacts (G0)

# Reflection-free view of the G0 `artifacts` table, kept out of `Base.metadata` so this module
# never creates or migrates it. Swap these two functions for the real model once it settles.
_ARTIFACTS_METADATA = MetaData()
ARTIFACTS = Table(
    "artifacts",
    _ARTIFACTS_METADATA,
    Column("id", String(32), primary_key=True),
    Column("project_id", String(32), nullable=False),
    Column("name", String(255), nullable=False),
    Column("media_type", String(127), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("verified", Boolean, nullable=False),
    Column("status", String(16), nullable=False),
    Column("owner_kind", String(32)),
    Column("owner_id", String(64)),
    Column("created_by", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
_ARTIFACT_FIELDS = (
    "id", "name", "media_type", "size", "sha256", "verified", "owner_kind", "owner_id", "created_at",
    "created_by",
)


def artifact_exists(session: Session, project_id: str, artifact_id: str) -> bool:
    found = session.execute(
        select(ARTIFACTS.c.id).where(ARTIFACTS.c.id == artifact_id, ARTIFACTS.c.project_id == project_id)
    ).first()
    return found is not None


def load_artifacts(session: Session, project_id: str, artifact_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """`Artifact` objects (contract G0) by id, only those of `project_id` that still exist."""
    if not artifact_ids:
        return {}
    cols = [ARTIFACTS.c[name] for name in _ARTIFACT_FIELDS]
    rows = session.execute(
        select(*cols).where(ARTIFACTS.c.project_id == project_id, ARTIFACTS.c.id.in_(list(artifact_ids)))
    ).mappings()
    out = {}
    for row in rows:
        item = dict(row)
        item["created_at"] = as_utc(item["created_at"])
        item["verified"] = bool(item["verified"])
        out[item["id"]] = item
    return out


# --------------------------------------------------------------------------- reaper


def reap_stale_runs(session: Session, now: datetime | None = None) -> int:
    """Mark `running` runs without a heartbeat (or start, if none) for > 5 min as failed.
    Idempotent: a second call finds nothing to do. The caller commits."""
    now = now or utcnow()
    cutoff = now - HEARTBEAT_TIMEOUT
    stale = and_(
        Run.status == "running",
        or_(Run.heartbeat_at < cutoff, and_(Run.heartbeat_at.is_(None), Run.started_at < cutoff)),
    )
    result = session.execute(
        update(Run)
        .where(stale)
        .values(status="failed", error=LOST_HEARTBEAT, ended_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def ensure_reaper_scheduled(
    session: Session,
    *,
    delay: timedelta = timedelta(0),
    exclude_job_id: str | None = None,
    count_running: bool = True,
) -> bool:
    """Enqueue a `runs.reaper` job unless one is already queued (or, with `count_running`, being
    run: a running reaper enqueues its own successor). Returns True if it enqueued one.

    On PostgreSQL an advisory transaction lock serializes the check-and-insert, so at most one
    reaper is ever queued. The caller commits."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _REAPER_LOCK_ID})
    statuses = ["queued", "running"] if count_running else ["queued"]
    query = select(func.count()).select_from(Job).where(Job.kind == REAPER_KIND, Job.status.in_(statuses))
    if exclude_job_id is not None:
        query = query.where(Job.id != exclude_job_id)
    if session.scalar(query):
        return False
    # One attempt: the successor enqueued by each run is the retry.
    jobs.enqueue(session, SYSTEM_PROJECT_ID, REAPER_KIND, {}, run_after=utcnow() + delay, max_attempts=1)
    return True
