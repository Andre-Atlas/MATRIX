"""Artifact rules shared by the API, the worker and other modules: name/type normalization, quota,
presigned completion, GC, and `store_bytes` (store bytes and create a ready `Artifact` row).

Quota: the bytes of the distinct blobs (`sha256`) a project's artifacts reference, pending uploads
included (they reserve space). Concurrent uploads can overshoot the quota by at most what is in
flight; the per-object limit always holds.

GC (`gc`): drops pending uploads older than `PENDING_TTL` (and their staged objects), purges stale
temp/staged objects, then deletes every blob no artifact row of its project references and whose
mtime is older than `GC_GRACE` (so a blob written by an upload that has not committed its row yet is
kept). A blob being re-referenced at the exact moment the GC deletes it remains a residual race;
the `size` checks on download/complete surface it as 404/409 instead of serving wrong content.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, BinaryIO

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.artifacts import CHUNK_SIZE, ArtifactStore, BlobNotFound, BlobTooLarge, UploadChanged
from matrix_server.artifacts.models import Artifact
from matrix_server.storage.db import as_utc, utcnow

GC_JOB_KIND = "artifacts.gc"
GC_JOB_PROJECT = "_system"
PENDING_TTL = timedelta(hours=24)
GC_GRACE = timedelta(hours=1)
GC_INTERVAL = timedelta(hours=1)
_GC_LOCK_ID = 0x6D78_6172_7467  # pg_advisory_xact_lock key ("mxartg")
MAX_NAME_CHARS = 255
DEFAULT_MEDIA_TYPE = "application/octet-stream"
INLINE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"})

_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")
_OWNER_KIND_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")
_OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


class ArtifactError(Exception):
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidInput(ArtifactError):
    status_code = 422


class TooLarge(ArtifactError):
    status_code = 413


class QuotaExceeded(ArtifactError):
    status_code = 413


class HashMismatch(ArtifactError):
    status_code = 422


class UploadMissing(ArtifactError):
    status_code = 409


@dataclass(frozen=True)
class Limits:
    max_artifact_bytes: int = 5 * 1024**3
    project_quota_bytes: int = 50 * 1024**3
    verify_hash_max_bytes: int = 256 * 1024**2

    @classmethod
    def from_settings(cls, settings: Any) -> Limits:
        return cls(
            max_artifact_bytes=settings.max_artifact_bytes,
            project_quota_bytes=settings.project_quota_bytes,
            verify_hash_max_bytes=settings.verify_hash_max_bytes,
        )


# --- normalization ------------------------------------------------------------------------------


def normalize_filename(raw: str | None) -> str:
    """Last path component, no control/format characters, at most 255 chars. Never used in paths."""
    if raw is None:
        raise InvalidInput("filename is required")
    name = unicodedata.normalize("NFC", raw)
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp"))
    name = re.split(r"[/\\]", name)
    name = next((part.strip() for part in reversed(name) if part.strip()), "")
    if name in ("", ".", ".."):
        raise InvalidInput("invalid filename")
    if len(name) > MAX_NAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 16 and stem:
            name = stem[: MAX_NAME_CHARS - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_NAME_CHARS]
    return name


def normalize_media_type(raw: str | None) -> str:
    if not raw:
        return DEFAULT_MEDIA_TYPE
    base = raw.split(";", 1)[0].strip().lower()
    if not _MEDIA_TYPE_RE.fullmatch(base):
        raise InvalidInput("invalid media type")
    return base


def normalize_owner(kind: str | None, owner_id: str | None) -> tuple[str | None, str | None]:
    kind = kind or None
    owner_id = owner_id or None
    if kind is not None and not _OWNER_KIND_RE.fullmatch(kind):
        raise InvalidInput("invalid owner_kind")
    if owner_id is not None and not _OWNER_ID_RE.fullmatch(owner_id):
        raise InvalidInput("invalid owner_id")
    if owner_id is not None and kind is None:
        raise InvalidInput("owner_id requires owner_kind")
    return kind, owner_id


def is_inline_type(media_type: str) -> bool:
    return media_type in INLINE_MEDIA_TYPES


# --- quota --------------------------------------------------------------------------------------


def _counted(now: datetime | None = None):
    """Rows that hold quota: ready artifacts and pending reservations that have not expired."""
    cutoff = (now or utcnow()) - PENDING_TTL
    return or_(Artifact.status == "ready", and_(Artifact.status == "pending", Artifact.created_at >= cutoff))


def project_usage(session: Session, project_id: str, *, exclude_sha256: str | None = None) -> int:
    """Bytes held by the project: one size per distinct blob (`sha256`) among ready artifacts and
    active pending reservations (by their declared size). `exclude_sha256` leaves one blob out, so
    a caller can add the size it is about to store for that digest without counting it twice."""
    stmt = select(Artifact.sha256, func.max(Artifact.size).label("size")).where(
        Artifact.project_id == project_id, _counted()
    )
    if exclude_sha256 is not None:
        stmt = stmt.where(Artifact.sha256 != exclude_sha256)
    per_blob = stmt.group_by(Artifact.sha256).subquery()
    return int(session.scalar(select(func.coalesce(func.sum(per_blob.c.size), 0))) or 0)


def blob_referenced(session: Session, project_id: str, sha256: str) -> bool:
    """Any row (ready or pending) points at the blob: it must not be deleted from the store."""
    return (
        session.scalar(
            select(Artifact.id).where(Artifact.project_id == project_id, Artifact.sha256 == sha256).limit(1)
        )
        is not None
    )


def blob_stored(session: Session, project_id: str, sha256: str, size: int = 0) -> bool:
    """A ready, verified artifact already holds this content (same digest, at least `size` bytes):
    storing it again adds nothing to the quota (dedup). Pending reservations never count here, so a
    reservation (e.g. of size 0) cannot be used to skip the quota check for the same digest."""
    return (
        session.scalar(
            select(Artifact.id)
            .where(
                Artifact.project_id == project_id,
                Artifact.sha256 == sha256,
                Artifact.status == "ready",
                Artifact.verified.is_(True),
                Artifact.size >= size,
            )
            .limit(1)
        )
        is not None
    )


def upload_budget(session: Session, project_id: str, limits: Limits, sha256: str | None = None) -> int:
    """Max bytes an upload may stream: the per-object limit, capped by the quota left — or by the
    largest ready blob already in the project, since content equal to a stored blob (dedup) adds
    nothing. A new blob that still goes over the quota is removed after hashing (`store_stream`)."""
    if sha256 and blob_stored(session, project_id, sha256):
        return limits.max_artifact_bytes
    remaining = limits.project_quota_bytes - project_usage(session, project_id, exclude_sha256=sha256)
    largest = (
        session.scalar(
            select(func.max(Artifact.size)).where(Artifact.project_id == project_id, Artifact.status == "ready")
        )
        or 0
    )
    return max(0, min(limits.max_artifact_bytes, max(remaining, int(largest))))


def check_quota_for(session: Session, project_id: str, sha256: str, size: int, limits: Limits) -> None:
    """Raise unless `size` bytes of content `sha256` fit: free when a ready artifact already stores
    it, else the usage of every other blob plus `size` must stay within the quota."""
    if size > limits.max_artifact_bytes:
        raise TooLarge("artifact exceeds the size limit")
    if blob_stored(session, project_id, sha256, size):
        return
    if project_usage(session, project_id, exclude_sha256=sha256) + size > limits.project_quota_bytes:
        raise QuotaExceeded("project artifact quota exceeded")


# --- storing --------------------------------------------------------------------------------------


def _ids(owner: Any) -> tuple[str, str | None]:
    """`Principal` → (project_id, created_by); or a plain project id / (project_id, created_by)."""
    if isinstance(owner, str):
        return owner, None
    if isinstance(owner, tuple):
        return owner[0], owner[1]
    return owner.project_id, created_by_of(owner)


def created_by_of(principal: Any) -> str | None:
    user_id = getattr(principal, "user_id", None)
    return f"user:{user_id}" if user_id else ("api_key" if getattr(principal, "kind", None) else None)


def store_stream(
    session: Session,
    store: ArtifactStore,
    owner: Any,
    name: str,
    stream: BinaryIO,
    media_type: str | None = None,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    *,
    limits: Limits | None = None,
    expected_sha256: str | None = None,
) -> Artifact:
    """Stream into the store and add a ready, verified `Artifact` to `session` (caller commits).

    Raises `TooLarge`/`QuotaExceeded` (413) or `HashMismatch` (422); on any error a blob this call
    created is removed again, so nothing is persisted."""
    limits = limits or Limits()
    project_id, created_by = _ids(owner)
    name = normalize_filename(name)
    media_type = normalize_media_type(media_type)
    owner_kind, owner_id = normalize_owner(owner_kind, owner_id)
    budget = upload_budget(session, project_id, limits, expected_sha256)
    quota_bound = budget < limits.max_artifact_bytes
    try:
        info = store.put(project_id, stream, max_bytes=budget)
    except BlobTooLarge as exc:
        if quota_bound:
            raise QuotaExceeded("project artifact quota exceeded") from exc
        raise TooLarge("artifact exceeds the size limit") from exc
    try:
        if expected_sha256 is not None and info.sha256 != expected_sha256:
            raise HashMismatch("sha256 does not match the content")
        # Always (also for a blob that already existed in the store: an orphan awaiting deletion,
        # or one only a pending reservation points at); `check_quota_for` handles real dedup.
        check_quota_for(session, project_id, info.sha256, info.size, limits)
        artifact = Artifact(
            project_id=project_id,
            name=name,
            media_type=media_type,
            size=info.size,
            sha256=info.sha256,
            verified=True,
            status="ready",
            owner_kind=owner_kind,
            owner_id=owner_id,
            created_by=created_by,
        )
        session.add(artifact)
        session.flush()
        return artifact
    except BaseException:
        if info.created and not blob_referenced(session, project_id, info.sha256):
            store.delete(project_id, info.sha256)
        raise


def store_bytes(
    session: Session,
    store: ArtifactStore,
    owner: Any,
    name: str,
    data: bytes,
    media_type: str | None = None,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    *,
    limits: Limits | None = None,
) -> Artifact:
    """Store `data` and add a ready `Artifact` row to `session` (the caller commits).

    `owner` is a `Principal`, a project id, or `(project_id, created_by)`."""
    return store_stream(
        session, store, owner, name, io.BytesIO(data), media_type, owner_kind, owner_id, limits=limits
    )


def hash_stream(stream: BinaryIO, limit: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if limit is not None and size > limit:
                break
            digest.update(chunk)
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    return digest.hexdigest(), size


def _discard(session: Session, store: ArtifactStore, artifact: Artifact, *, staged: bool, blob: bool) -> None:
    """Remove a failed pending upload: its staged object, its row and, when `blob`, the blob it
    wrote under its digest if nothing else in the project points at it (the caller commits)."""
    pid, sha = artifact.project_id, artifact.sha256
    if staged:
        store.delete_upload(pid, artifact.id)
    session.delete(artifact)
    session.flush()
    if blob and not blob_referenced(session, pid, sha):
        store.delete(pid, sha)


def complete_upload(session: Session, store: ArtifactStore, artifact: Artifact, limits: Limits) -> Artifact:
    """Settle a pending presigned upload: check the staged object's size and prove its sha256, then
    move it to the blob key and mark the artifact ready. Idempotent for a ready artifact.

    Invariant (audit S11): a blob key `{project}/{aa}/{sha256}` only ever holds content proven to
    hash to `sha256` — hashed by `put`, re-hashed here up to `verify_hash_max_bytes`, or, above it,
    checked by the S3 server (`x-amz-checksum-sha256`, signed into the presigned PUT; trusted only
    with `MATRIX_S3_TRUST_CHECKSUM=1`). The staged object is pinned to the version (ETag) seen by
    the size check: it is hashed with `If-Match` and promoted with `CopySourceIfMatch`, so a client
    re-PUTting the presigned URL between the hash and the copy makes the promotion fail
    (`UploadChanged`) instead of promoting unverified content. An object that cannot be proven is
    never promoted: it is removed with its row and `HashMismatch` (422) is raised; the client can
    use the direct upload, which hashes any size. `UploadMissing` (409) when nothing was uploaded
    yet. The quota is checked again with the size actually received (`QuotaExceeded`, 413; the
    upload and its row are removed)."""
    if artifact.status == "ready":
        return artifact
    pid, sha, size = artifact.project_id, artifact.sha256, artifact.size
    staged = store.stat_upload_info(pid, artifact.id)
    if staged is not None:
        detail = None
        if staged.size != size:  # audit S10: never accept more (or less) than was declared and reserved
            detail = "uploaded object does not match the declared size"
        elif size <= limits.verify_hash_max_bytes:
            try:
                got, read = hash_stream(store.open_upload(pid, artifact.id, etag=staged.etag), size)
            except (BlobNotFound, UploadChanged):
                got, read = "", -1
            if got != sha or read != size:
                detail = "uploaded object does not match the declared sha256"
        elif store.upload_checksum(pid, artifact.id, etag=staged.etag) != sha:
            detail = (
                "uploaded object is larger than MATRIX_VERIFY_HASH_MAX_BYTES and its sha256 cannot be "
                "verified; use the direct upload (POST /api/v1/artifacts)"
            )
        if detail is None:
            try:
                check_quota_for(session, pid, sha, staged.size, limits)
            except ArtifactError:
                _discard(session, store, artifact, staged=True, blob=False)
                raise
            try:
                store.promote_upload(pid, artifact.id, sha, etag=staged.etag)
            except UploadChanged:
                detail = "uploaded object changed while it was being verified"
        if detail is not None:
            _discard(session, store, artifact, staged=True, blob=False)
            raise HashMismatch(detail)
    else:
        # Content already stored under this digest (dedup within the project, or the local
        # backend's content route, which hashes while writing).
        existing = store.size(pid, sha)
        if existing is None:
            raise UploadMissing("upload not received")
        if existing != size:
            session.delete(artifact)
            session.flush()
            raise HashMismatch("uploaded object does not match the declared size")
        if size <= limits.verify_hash_max_bytes:  # defence in depth; blob keys are verified by construction
            try:
                got, _ = hash_stream(store.open(pid, sha), size)
            except BlobNotFound as exc:
                raise UploadMissing("upload not received") from exc
            if got != sha:
                session.delete(artifact)
                session.flush()
                raise HashMismatch("stored object does not match the declared sha256")
        try:
            check_quota_for(session, pid, sha, existing, limits)
        except ArtifactError:
            _discard(session, store, artifact, staged=False, blob=True)
            raise
    artifact.status = "ready"
    artifact.verified = True
    session.flush()
    return artifact


# --- GC -------------------------------------------------------------------------------------------


@dataclass
class GcResult:
    expired_uploads: int = 0
    purged_objects: int = 0
    deleted_blobs: int = 0
    kept_blobs: int = 0


def gc(
    session: Session,
    store: ArtifactStore,
    *,
    now: datetime | None = None,
    pending_ttl: timedelta = PENDING_TTL,
    grace: timedelta = GC_GRACE,
) -> GcResult:
    now = now or utcnow()
    result = GcResult()
    expired = session.scalars(
        select(Artifact).where(Artifact.status == "pending", Artifact.created_at < now - pending_ttl)
    ).all()
    for artifact in expired:
        store.delete_upload(artifact.project_id, artifact.id)
    if expired:
        session.execute(delete(Artifact).where(Artifact.id.in_([a.id for a in expired])))
        session.commit()
    result.expired_uploads = len(expired)
    result.purged_objects = store.purge_stale(now - pending_ttl)

    referenced: dict[str, set[str]] = {}
    for ref in store.iter_blobs():
        if ref.project_id not in referenced:
            referenced[ref.project_id] = set(
                session.scalars(
                    select(Artifact.sha256).where(Artifact.project_id == ref.project_id).distinct()
                ).all()
            )
        modified = as_utc(ref.modified)
        if ref.sha256 in referenced[ref.project_id] or (modified is not None and modified > now - grace):
            result.kept_blobs += 1
            continue
        # Re-check just before deleting: a row may have been committed since the snapshot.
        if blob_referenced(session, ref.project_id, ref.sha256):
            result.kept_blobs += 1
            continue
        store.delete(ref.project_id, ref.sha256)
        result.deleted_blobs += 1
    return result


def ensure_gc_scheduled(
    session: Session,
    *,
    delay: timedelta = timedelta(0),
    exclude_job_id: str | None = None,
    count_running: bool = True,
) -> bool:
    """Enqueue an `artifacts.gc` job unless one is already queued (or, with `count_running`, being
    run: a running GC enqueues its own successor `GC_INTERVAL` later). Same self-rescheduling
    mechanism as the `runs.reaper` job; the worker calls it at start-up. On PostgreSQL an advisory
    transaction lock serializes the check-and-insert. The caller commits. True if it enqueued one."""
    from matrix_server.storage import jobs
    from matrix_server.storage.models import Job

    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _GC_LOCK_ID})
    statuses = ["queued", "running"] if count_running else ["queued"]
    query = select(func.count()).select_from(Job).where(Job.kind == GC_JOB_KIND, Job.status.in_(statuses))
    if exclude_job_id is not None:
        query = query.where(Job.id != exclude_job_id)
    if session.scalar(query):
        return False
    jobs.enqueue(session, GC_JOB_PROJECT, GC_JOB_KIND, {}, run_after=utcnow() + delay, max_attempts=1)
    return True


def schedule_gc(session_factory: sessionmaker[Session] | Callable[[], Session], *, run_after=None) -> bool:
    """Enqueue an `artifacts.gc` job unless one is already queued or running. Returns True if added."""
    delay = timedelta(0) if run_after is None else max(timedelta(0), run_after - utcnow())
    with session_factory() as session, session.begin():
        return ensure_gc_scheduled(session, delay=delay)
