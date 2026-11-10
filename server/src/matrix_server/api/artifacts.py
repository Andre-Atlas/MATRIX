"""Artifacts API (contract G0 section 2, ADR-007).

Raw upload bodies (`POST /api/v1/artifacts`, `PUT /api/v1/artifacts/{id}/content`) are exempt from
the JSON body limit (limits.py) and streamed into the store from a worker thread through a blocking
reader over the ASGI body, so a file is never buffered in memory; the store enforces the per-object
limit and the quota while reading. File names (`X-Filename`, percent-decoded as UTF-8) are
normalized and never reach a disk path or an S3 key.
"""

from __future__ import annotations

import base64
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any
from urllib.parse import unquote

import anyio
import anyio.from_thread
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from matrix_server import audit
from matrix_server.artifacts import (
    CHUNK_SIZE,
    ArtifactStore,
    BlobNotFound,
    BlobTooLarge,
    build_artifact_store,
    content_disposition,
    is_sha256,
)
from matrix_server.artifacts.models import Artifact
from matrix_server.artifacts.service import (
    PENDING_TTL,
    ArtifactError,
    HashMismatch,
    Limits,
    check_quota_for,
    complete_upload,
    created_by_of,
    is_inline_type,
    normalize_filename,
    normalize_media_type,
    normalize_owner,
    store_stream,
)
from matrix_server.auth import Principal, Role, require
from matrix_server.storage.db import as_utc, get_db, utcnow

DbSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[Principal, Depends(require(Role.VIEWER, scope="read"))]
Member = Annotated[Principal, Depends(require(Role.MEMBER, scope="write"))]

router = APIRouter(prefix="/api/v1", tags=["artifacts"])

MAX_PAGE = 200
_store_lock = threading.Lock()


def get_store(app: Any) -> ArtifactStore:
    """The app's store, built on first use from `app.state.settings` (tests may set it directly)."""
    store = getattr(app.state, "artifact_store", None)
    if store is None:
        with _store_lock:
            store = getattr(app.state, "artifact_store", None)
            if store is None:
                store = build_artifact_store(app.state.settings)
                app.state.artifact_store = store
    return store


def _limits(app: Any) -> Limits:
    return Limits.from_settings(app.state.settings)


def iso(ts: datetime | None) -> str | None:
    ts = as_utc(ts)
    return None if ts is None else ts.isoformat().replace("+00:00", "Z")


def artifact_out(a: Artifact) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "media_type": a.media_type,
        "size": a.size,
        "sha256": a.sha256,
        "verified": a.verified,
        "status": a.status,
        "owner_kind": a.owner_kind,
        "owner_id": a.owner_id,
        "created_at": iso(a.created_at),
        "created_by": a.created_by,
    }


def _http(exc: ArtifactError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _load(db: Session, principal: Principal, artifact_id: str) -> Artifact:
    artifact = db.scalar(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == principal.project_id)
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return artifact


class _BodyReader:
    """Blocking `read(n)` over the ASGI request stream, for use from a worker thread."""

    def __init__(self, request: Request) -> None:
        self._iter = request.stream().__aiter__()
        self._buf = bytearray()
        self._eof = False

    async def _next(self) -> bytes | None:
        try:
            return await self._iter.__anext__()
        except StopAsyncIteration:
            return None

    def read(self, n: int = -1) -> bytes:
        while not self._eof and (n < 0 or len(self._buf) < n):
            chunk = anyio.from_thread.run(self._next)
            if chunk is None:
                self._eof = True
            else:
                self._buf += chunk
        n = len(self._buf) if n < 0 else min(n, len(self._buf))
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def _header_sha(request: Request) -> str | None:
    raw = request.headers.get("x-sha256")
    if raw is None:
        return None
    value = raw.strip().lower()
    if not is_sha256(value):
        raise HTTPException(status_code=422, detail="X-Sha256 must be a hex sha256 digest")
    return value


# --- direct upload --------------------------------------------------------------------------------


def _store_direct(app: Any, principal: Principal, reader: _BodyReader, **kw: Any) -> dict[str, Any]:
    session: Session = app.state.session_factory()
    try:
        artifact = store_stream(
            session, get_store(app), principal, stream=reader, limits=_limits(app), **kw
        )
        session.commit()
        return artifact_out(artifact)
    except ArtifactError as exc:
        session.rollback()
        raise _http(exc) from None
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@router.post("/artifacts", status_code=201)
async def upload_artifact(request: Request, principal: Member) -> JSONResponse:
    headers = request.headers
    raw_name = headers.get("x-filename")
    try:
        name = normalize_filename(unquote(raw_name, errors="strict") if raw_name is not None else None)
        media_type = normalize_media_type(headers.get("content-type"))
        owner_kind, owner_id = normalize_owner(headers.get("x-owner-kind"), headers.get("x-owner-id"))
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="invalid filename") from None
    except ArtifactError as exc:
        raise _http(exc) from None
    expected = _header_sha(request)
    out = await anyio.to_thread.run_sync(
        partial(
            _store_direct,
            request.app,
            principal,
            _BodyReader(request),
            name=name,
            media_type=media_type,
            owner_kind=owner_kind,
            owner_id=owner_id,
            expected_sha256=expected,
        )
    )
    return JSONResponse(out, status_code=201)


# --- presigned flow -------------------------------------------------------------------------------


class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=4096)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    media_type: str | None = Field(default=None, max_length=255)
    owner_kind: str | None = None
    owner_id: str | None = None


@router.post("/artifacts/uploads", status_code=201)
def create_upload(body: UploadCreate, request: Request, principal: Member, db: DbSession) -> dict[str, Any]:
    app = request.app
    limits = _limits(app)
    sha = body.sha256.lower()
    try:
        name = normalize_filename(body.filename)
        media_type = normalize_media_type(body.media_type)
        owner_kind, owner_id = normalize_owner(body.owner_kind, body.owner_id)
        check_quota_for(db, principal.project_id, sha, body.size, limits)
    except ArtifactError as exc:
        raise _http(exc) from None
    artifact = Artifact(
        project_id=principal.project_id,
        name=name,
        media_type=media_type,
        size=body.size,
        sha256=sha,
        verified=False,
        status="pending",
        owner_kind=owner_kind,
        owner_id=owner_id,
        created_by=created_by_of(principal),
        created_at=utcnow(),
    )
    db.add(artifact)
    db.flush()
    ttl = min(app.state.settings.artifact_url_ttl_seconds, int(PENDING_TTL.total_seconds()))
    presigned = get_store(app).presign_put(principal.project_id, sha, body.size, ttl, upload_id=artifact.id)
    if presigned is None:
        upload = {
            "method": "PUT",
            "url": f"{str(request.base_url).rstrip('/')}/api/v1/artifacts/{artifact.id}/content",
            "headers": {"Content-Type": "application/octet-stream"},
            "expires_at": iso(as_utc(artifact.created_at) + PENDING_TTL),
        }
    else:
        upload = {
            "method": presigned.method,
            "url": presigned.url,
            "headers": presigned.headers,
            "expires_at": iso(presigned.expires_at),
        }
    return {"artifact": artifact_out(artifact), "upload": upload}


def _is_expired(artifact: Artifact) -> bool:
    return artifact.status == "pending" and as_utc(artifact.created_at) < utcnow() - PENDING_TTL


def _put_content(app: Any, principal: Principal, artifact_id: str, reader: _BodyReader) -> None:
    session: Session = app.state.session_factory()
    store = get_store(app)
    try:
        artifact = _load(session, principal, artifact_id)
        if artifact.status != "pending" or _is_expired(artifact):
            raise HTTPException(status_code=409, detail="artifact is not awaiting an upload")
        pid, sha, size = artifact.project_id, artifact.sha256, artifact.size
        try:
            info = store.put(pid, reader, max_bytes=size)
        except BlobTooLarge:
            raise HTTPException(status_code=422, detail="content exceeds the declared size") from None
        if info.sha256 != sha or info.size != size:
            if info.created:
                others = session.scalar(
                    select(Artifact.id)
                    .where(Artifact.project_id == pid, Artifact.sha256 == info.sha256, Artifact.id != artifact.id)
                    .limit(1)
                )
                if others is None:
                    store.delete(pid, info.sha256)
            raise HTTPException(status_code=422, detail="content does not match the declared size or sha256")
        session.commit()
    finally:
        session.close()


@router.put("/artifacts/{artifact_id}/content", status_code=204)
async def put_content(artifact_id: str, request: Request, principal: Member) -> Response:
    await anyio.to_thread.run_sync(
        partial(_put_content, request.app, principal, artifact_id, _BodyReader(request))
    )
    return Response(status_code=204)


@router.post("/artifacts/{artifact_id}/complete")
def complete(artifact_id: str, request: Request, principal: Member, db: DbSession) -> dict[str, Any]:
    artifact = _load(db, principal, artifact_id)
    if _is_expired(artifact):
        raise HTTPException(status_code=410, detail="upload expired")
    try:
        complete_upload(db, get_store(request.app), artifact, _limits(request.app))
    except HashMismatch as exc:
        db.commit()  # the failed upload's row is removed
        raise _http(exc) from None
    except ArtifactError as exc:
        raise _http(exc) from None
    return artifact_out(artifact)


# --- read -----------------------------------------------------------------------------------------


def _encode_cursor(a: Artifact) -> str:
    return base64.urlsafe_b64encode(f"{iso(a.created_at)}|{a.id}".encode()).decode()


def _decode_cursor(db: Session, cursor: str) -> tuple[datetime, str]:
    try:
        stamp, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        ts = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    ts = ts.astimezone(UTC)
    if db.get_bind().dialect.name == "sqlite":
        ts = ts.replace(tzinfo=None)
    return ts, row_id


@router.get("/artifacts")
def list_artifacts(
    principal: Viewer,
    db: DbSession,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Ready artifacts, newest first, keyset-paginated on (created_at, id)."""
    try:
        owner_kind, owner_id = normalize_owner(owner_kind, owner_id)
    except ArtifactError as exc:
        raise _http(exc) from None
    stmt = select(Artifact).where(Artifact.project_id == principal.project_id, Artifact.status == "ready")
    if owner_kind is not None:
        stmt = stmt.where(Artifact.owner_kind == owner_kind)
    if owner_id is not None:
        stmt = stmt.where(Artifact.owner_id == owner_id)
    if cursor:
        ts, row_id = _decode_cursor(db, cursor)
        stmt = stmt.where(or_(Artifact.created_at < ts, and_(Artifact.created_at == ts, Artifact.id < row_id)))
    rows = list(db.scalars(stmt.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])
    return {"items": [artifact_out(a) for a in rows], "next_cursor": next_cursor}


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, principal: Viewer, db: DbSession) -> dict[str, Any]:
    return artifact_out(_load(db, principal, artifact_id))


def _iter_file(stream: Any) -> Iterator[bytes]:
    try:
        while chunk := stream.read(CHUNK_SIZE):
            yield chunk
    finally:
        stream.close()


@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, request: Request, principal: Viewer, db: DbSession, inline: bool = False):
    artifact = _load(db, principal, artifact_id)
    if artifact.status != "ready":
        raise HTTPException(status_code=409, detail="artifact upload not completed")
    store = get_store(request.app)
    serve_inline = inline and is_inline_type(artifact.media_type)
    security = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "private, no-store",
    }
    if not serve_inline:
        ttl = request.app.state.settings.artifact_url_ttl_seconds
        presigned = store.presign_get(
            artifact.project_id, artifact.sha256, ttl, filename=artifact.name, media_type=artifact.media_type
        )
        if presigned is not None:
            return Response(status_code=302, headers={"Location": presigned.url, **security})
    try:
        stream = store.open(artifact.project_id, artifact.sha256)
    except BlobNotFound:
        raise HTTPException(status_code=404, detail="artifact content not found") from None
    headers = {
        **security,
        "Content-Disposition": content_disposition(artifact.name, inline=serve_inline),
        "Content-Length": str(artifact.size),
    }
    return StreamingResponse(_iter_file(stream), media_type=artifact.media_type, headers=headers)


@router.delete("/artifacts/{artifact_id}", status_code=204)
def delete_artifact(artifact_id: str, principal: Member, db: DbSession) -> Response:
    artifact = _load(db, principal, artifact_id)
    audit.record(
        db,
        action="artifact.delete",
        project_id=principal.project_id,
        actor_kind=principal.kind,
        actor_id=principal.user_id,
        target=artifact.id,
        detail={"sha256": artifact.sha256, "size": artifact.size, "status": artifact.status},
    )
    db.delete(artifact)
    return Response(status_code=204)
