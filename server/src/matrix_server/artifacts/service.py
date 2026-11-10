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

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.artifacts import CHUNK_SIZE, ArtifactStore, BlobNotFound, BlobTooLarge
from matrix_server.artifacts.models import Artifact
from matrix_server.storage.db import as_utc, utcnow

GC_JOB_KIND = "artifacts.gc"
GC_JOB_PROJECT = "_system"
PENDING_TTL = timedelta(hours=24)
GC_GRACE = timedelta(hours=1)
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


def project_usage(session: Session, project_id: str) -> int:
    per_blob = (
        select(Artifact.sha256, func.max(Artifact.size).label("size"))
        .where(Artifact.project_id == project_id)
        .group_by(Artifact.sha256)
        .subquery()
    )
    return int(session.scalar(select(func.coalesce(func.sum(per_blob.c.size), 0))) or 0)


def blob_referenced(session: Session, project_id: str, sha256: str) -> bool:
    return (
        session.scalar(
            select(Artifact.id).where(Artifact.project_id == project_id, Artifact.sha256 == sha256).limit(1)
        )
        is not None
    )


def upload_budget(session: Session, project_id: str, limits: Limits, sha256: str | None = None) -> int:
    """Max bytes an upload may stream: the per-object limit, capped by the quota left — or by the
    largest blob already in the project, since content equal to a stored blob (dedup) adds nothing.
    A new blob that still goes over the quota is removed after hashing (`store_stream`)."""
    if sha256 and blob_referenced(session, project_id, sha256):
        return limits.max_artifact_bytes
    remaining = limits.project_quota_bytes - project_usage(session, project_id)
    largest = session.scalar(select(func.max(Artifact.size)).where(Artifact.project_id == project_id)) or 0
    return max(0, min(limits.max_artifact_bytes, max(remaining, int(largest))))


def check_quota_for(session: Session, project_id: str, sha256: str, size: int, limits: Limits) -> None:
    if size > limits.max_artifact_bytes:
        raise TooLarge("artifact exceeds the size limit")
    if blob_referenced(session, project_id, sha256):
        return
    if project_usage(session, project_id) + size > limits.project_quota_bytes:
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
        if info.created:
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


def complete_upload(session: Session, store: ArtifactStore, artifact: Artifact, limits: Limits) -> Artifact:
    """Settle a pending presigned upload: check size (and the hash up to `verify_hash_max_bytes`),
    move the staged object into place and mark the artifact ready. On a mismatch the staged object
    and the row are removed and `HashMismatch` (422) is raised; `UploadMissing` (409) when nothing
    was uploaded yet. Idempotent for an already ready artifact."""
    if artifact.status == "ready":
        return artifact
    pid, sha, size = artifact.project_id, artifact.sha256, artifact.size
    verify = size <= limits.verify_hash_max_bytes
    staged = store.stat_upload(pid, artifact.id)
    if staged is not None:
        ok = staged == size
        if ok and verify:
            got, read = hash_stream(store.open_upload(pid, artifact.id), size)
            ok = got == sha and read == size
        if not ok:
            store.delete_upload(pid, artifact.id)
            session.delete(artifact)
            session.flush()
            raise HashMismatch("uploaded object does not match the declared size or sha256")
        store.promote_upload(pid, artifact.id, sha)
    else:
        existing = store.size(pid, sha)
        if existing is None:
            raise UploadMissing("upload not received")
        if existing != size:
            # A different blob size under this digest: never trust or delete the shared blob here.
            session.delete(artifact)
            session.flush()
            raise HashMismatch("uploaded object does not match the declared size")
        if verify:
            try:
                got, _ = hash_stream(store.open(pid, sha), size)
            except BlobNotFound as exc:
                raise UploadMissing("upload not received") from exc
            if got != sha:
                session.delete(artifact)
                session.flush()
                raise HashMismatch("stored object does not match the declared sha256")
    artifact.status = "ready"
    artifact.verified = verify
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


def schedule_gc(session_factory: sessionmaker[Session] | Callable[[], Session], *, run_after=None) -> bool:
    """Enqueue an `artifacts.gc` job unless one is already queued or running. Returns True if added."""
    from matrix_server.storage import jobs
    from matrix_server.storage.models import Job

    with session_factory() as session, session.begin():
        pending = session.scalar(
            select(Job.id).where(Job.kind == GC_JOB_KIND, Job.status.in_(("queued", "running"))).limit(1)
        )
        if pending is not None:
            return False
        jobs.enqueue(session, GC_JOB_PROJECT, GC_JOB_KIND, {}, run_after=run_after, max_attempts=1)
        return True
