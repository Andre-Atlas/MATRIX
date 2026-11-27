"""File data-sets API (contract G3 section 2): `/api/v1/data-sets`.

Distinct from `/datasets` (evaluation items). A data-set is a named list of immutable versions;
each version is a manifest of files (`path`, `sha256`, `size`) bound to blobs of the project's
artifact store, which is where the bytes are uploaded first (`POST /api/v1/artifacts`, deduplicated
by content). Reads need VIEWER, writes MEMBER, deleting a data-set ADMIN. File downloads follow
`GET /api/v1/artifacts/{id}/download` (same security headers, presigned redirect on S3, inline only
for the safe image types).
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server import audit
from matrix_server.api.artifacts import _iter_file, get_store
from matrix_server.artifacts import BlobNotFound, content_disposition
from matrix_server.artifacts.models import Artifact
from matrix_server.artifacts.service import is_inline_type
from matrix_server.auth import Principal, Role, require
from matrix_server.data_sets import service
from matrix_server.data_sets.models import DataSet, DataSetFile, DataSetTag, DataSetVersion
from matrix_server.data_sets.service import DataSetError
from matrix_server.storage.db import get_db, utcnow

DbSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[Principal, Depends(require(Role.VIEWER, scope="read"))]
Member = Annotated[Principal, Depends(require(Role.MEMBER, scope="write"))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]

router = APIRouter(prefix="/api/v1", tags=["data-sets"])

MAX_PAGE = 1000
MAX_MISSING_CHECK = 10_000


def _http(exc: DataSetError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _created_by(principal: Principal) -> str | None:
    return f"user:{principal.user_id}" if principal.user_id else ("api_key" if principal.kind else None)


def _audit(db: Session, principal: Principal, action: str, target: str, detail: dict[str, Any]) -> None:
    audit.record(
        db,
        action=action,
        project_id=principal.project_id,
        actor_kind=principal.actor_kind,
        actor_id=principal.actor_id,
        target=target,
        detail=detail,
    )


def _load(db: Session, principal: Principal, name: str, *, lock: bool = False) -> DataSet:
    try:
        return service.get_data_set(db, principal.project_id, name, lock=lock)
    except DataSetError as exc:
        raise _http(exc) from None


def _resolve(db: Session, ds: DataSet, ref: str) -> DataSetVersion:
    try:
        return service.resolve_version(db, ds, ref)
    except DataSetError as exc:
        raise _http(exc) from None


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def _decode(cursor: str) -> str:
    try:
        return base64.b64decode(cursor.encode(), altchars=b"-_", validate=True).decode()
    except (ValueError, UnicodeError):  # binascii.Error is a ValueError
        raise HTTPException(status_code=400, detail="invalid cursor") from None


def _version_out(v: DataSetVersion, ds: DataSet, tags: list[str], is_latest: bool) -> dict[str, Any]:
    all_tags = sorted(tags + ([service.LATEST] if is_latest else []))
    return {
        "id": v.id,
        "dataset": ds.name,
        "data_set_id": ds.id,
        "version": v.version,
        "parent_version": v.parent_version,
        "manifest_sha256": v.manifest_sha256,
        "file_count": v.file_count,
        "total_bytes": v.total_bytes,
        "message": v.message,
        "source": {"type": v.source_type, "ref": v.source_ref},
        "tags": all_tags,
        "created_at": service.iso(v.created_at),
        "created_by": v.created_by,
    }


def _file_out(f: DataSetFile) -> dict[str, Any]:
    return {
        "path": f.path,
        "sha256": f.sha256,
        "size": f.size,
        "artifact_id": f.artifact_id,
        "media_type": f.media_type,
    }


def _data_set_out(db: Session, ds: DataSet, stats: tuple[int, int] | None = None) -> dict[str, Any]:
    if stats is None:
        row = db.execute(
            select(func.count(), func.coalesce(func.max(DataSetVersion.version), 0)).where(
                DataSetVersion.data_set_id == ds.id
            )
        ).one()
        stats = (int(row[0]), int(row[1]))
    latest = None
    if stats[0]:
        v = service.latest_version(db, ds.id)
        latest = None if v is None else {
            "version": v.version,
            "file_count": v.file_count,
            "total_bytes": v.total_bytes,
            "created_at": service.iso(v.created_at),
        }
    return {
        "id": ds.id,
        "name": ds.name,
        "kind": ds.kind,
        "description": ds.description,
        "version_count": stats[0],
        "latest_version": latest,
        "created_at": service.iso(ds.created_at),
        "created_by": ds.created_by,
    }


# --- data-sets -------------------------------------------------------------------------------------


class DataSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["files"] = "files"
    description: str | None = Field(default=None, max_length=4000)


class FilesCheck(BaseModel):
    sha256: list[str] = Field(min_length=1, max_length=MAX_MISSING_CHECK)


@router.post("/data-sets/_files/missing")
def missing_files(body: FilesCheck, principal: Member, db: DbSession) -> dict[str, Any]:
    """Which of these content digests the project's artifact store does not have yet (a client
    uploads only those before creating a version)."""
    wanted = sorted({s.lower() for s in body.sha256})
    have: set[str] = set()
    for i in range(0, len(wanted), 500):
        have.update(
            db.scalars(
                select(Artifact.sha256).where(
                    Artifact.project_id == principal.project_id,
                    Artifact.sha256.in_(wanted[i : i + 500]),
                    Artifact.status == "ready",
                    Artifact.verified.is_(True),
                )
            )
        )
    return {"missing": [s for s in wanted if s not in have]}


@router.post("/data-sets", status_code=201)
def create_data_set(body: DataSetCreate, principal: Member, db: DbSession) -> dict[str, Any]:
    try:
        name = service.normalize_name(body.name)
    except DataSetError as exc:
        raise _http(exc) from None
    if db.scalar(select(DataSet.id).where(DataSet.project_id == principal.project_id, DataSet.name == name)):
        raise HTTPException(status_code=409, detail="data-set already exists")
    ds = DataSet(
        project_id=principal.project_id,
        name=name,
        kind=body.kind,
        description=body.description,
        created_by=_created_by(principal),
    )
    db.add(ds)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="data-set already exists") from None
    _audit(db, principal, "data_set.create", ds.id, {"name": ds.name, "kind": ds.kind})
    return _data_set_out(db, ds, (0, 0))


@router.get("/data-sets")
def list_data_sets(
    principal: Viewer,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    stmt = select(DataSet).where(DataSet.project_id == principal.project_id)
    if cursor:
        stmt = stmt.where(DataSet.name > _decode(cursor))
    rows = list(db.scalars(stmt.order_by(DataSet.name).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode(rows[-1].name)
    stats = {
        ds_id: (int(n), int(top))
        for ds_id, n, top in db.execute(
            select(DataSetVersion.data_set_id, func.count(), func.max(DataSetVersion.version))
            .where(DataSetVersion.data_set_id.in_([r.id for r in rows]))
            .group_by(DataSetVersion.data_set_id)
        )
    }
    return {
        "items": [_data_set_out(db, ds, stats.get(ds.id, (0, 0))) for ds in rows],
        "next_cursor": next_cursor,
    }


@router.get("/data-sets/{name}")
def get_data_set(name: str, principal: Viewer, db: DbSession) -> dict[str, Any]:
    ds = _load(db, principal, name)
    out = _data_set_out(db, ds)
    out["tags"] = {t: v for v, ts in service.tags_by_version(db, ds.id).items() for t in ts}
    return out


@router.delete("/data-sets/{name}", status_code=204)
def delete_data_set(
    name: str, principal: Admin, db: DbSession, force: bool = False
) -> Response:
    """Delete the data-set, its versions, manifests and tags (the artifacts stay). Refused (409)
    while runs reference one of its versions, unless `force=true`."""
    ds = _load(db, principal, name, lock=True)
    runs = service.referenced_by_runs(db, principal.project_id, ds.id)
    if runs and not force:
        raise HTTPException(status_code=409, detail=f"{runs} run(s) reference versions of this data-set")
    versions = int(db.scalar(select(func.count()).select_from(DataSetVersion).where(DataSetVersion.data_set_id == ds.id)) or 0)
    _audit(db, principal, "data_set.delete", ds.id, {"name": ds.name, "versions": versions, "runs": runs})
    # Children first: the in-session cascade would otherwise load every file row.
    ids = select(DataSetVersion.id).where(DataSetVersion.data_set_id == ds.id)
    db.execute(DataSetFile.__table__.delete().where(DataSetFile.version_id.in_(ids)))
    db.execute(DataSetTag.__table__.delete().where(DataSetTag.data_set_id == ds.id))
    db.execute(DataSetVersion.__table__.delete().where(DataSetVersion.data_set_id == ds.id))
    db.execute(DataSet.__table__.delete().where(DataSet.id == ds.id))
    return Response(status_code=204)


# --- versions --------------------------------------------------------------------------------------


class ManifestEntryIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    artifact_id: str | None = Field(default=None, max_length=64)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    size: int | None = Field(default=None, ge=0)
    media_type: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _has_reference(self) -> ManifestEntryIn:
        if not self.artifact_id and not self.sha256:
            raise ValueError("each file needs artifact_id or sha256")
        return self


class VersionCreate(BaseModel):
    message: str | None = Field(default=None, max_length=4000)
    parent_version: int | None = Field(default=None, ge=1)
    files: list[ManifestEntryIn]


@router.post("/data-sets/{name}/versions", status_code=201)
def create_version(
    name: str, body: VersionCreate, request: Request, principal: Member, db: DbSession, response: Response
) -> dict[str, Any]:
    """New immutable version from a manifest of files already in the artifact store. A manifest
    identical to the latest version's returns that version (200) instead of creating another."""
    ds = _load(db, principal, name, lock=True)
    # Held until the commit: an artifact DELETE takes the same lock before dropping a referenced blob.
    service.lock_for_new_version(db, principal.project_id)
    try:
        files = service.resolve_manifest(
            db, principal.project_id, body.files, limit=service.max_files(request.app.state.settings)
        )
        version, created = service.create_version(
            db, ds, files, message=body.message, parent_version=body.parent_version,
            created_by=_created_by(principal),
        )
    except DataSetError as exc:
        raise _http(exc) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="concurrent version creation; retry") from None
    if created:
        _audit(
            db, principal, "data_set.version.create", version.id,
            {"data_set": ds.name, "version": version.version, "files": version.file_count,
             "bytes": version.total_bytes, "manifest_sha256": version.manifest_sha256},
        )
    else:
        response.status_code = 200
    latest = service.latest_version(db, ds.id)
    tags = service.tags_by_version(db, ds.id).get(version.version, [])
    return _version_out(version, ds, tags, latest is not None and latest.id == version.id)


@router.get("/data-sets/{name}/versions")
def list_versions(
    name: str,
    principal: Viewer,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Newest first; `cursor` is the last version number of the previous page."""
    ds = _load(db, principal, name)
    stmt = select(DataSetVersion).where(DataSetVersion.data_set_id == ds.id)
    if cursor is not None:
        stmt = stmt.where(DataSetVersion.version < cursor)
    rows = list(db.scalars(stmt.order_by(DataSetVersion.version.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = rows[-1].version
    latest = service.latest_version(db, ds.id)
    tags = service.tags_by_version(db, ds.id)
    return {
        "items": [
            _version_out(v, ds, tags.get(v.version, []), latest is not None and latest.id == v.id) for v in rows
        ],
        "next_cursor": next_cursor,
    }


@router.get("/data-sets/{name}/versions/{version}")
def get_version(
    name: str,
    version: str,
    principal: Viewer,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 200,
    cursor: str | None = None,
    prefix: Annotated[str | None, Query(max_length=1024)] = None,
) -> dict[str, Any]:
    """`{version, files}`; files ordered by path, keyset-paginated (`cursor` = opaque last path)."""
    ds = _load(db, principal, name)
    v = _resolve(db, ds, version)
    stmt = select(DataSetFile).where(DataSetFile.version_id == v.id)
    if prefix:
        stmt = stmt.where(DataSetFile.path.startswith(prefix, autoescape=True))
    if cursor:
        stmt = stmt.where(DataSetFile.path > _decode(cursor))
    rows = list(db.scalars(stmt.order_by(DataSetFile.path).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode(rows[-1].path)
    latest = service.latest_version(db, ds.id)
    tags = service.tags_by_version(db, ds.id).get(v.version, [])
    return {
        "version": _version_out(v, ds, tags, latest is not None and latest.id == v.id),
        "files": {"items": [_file_out(f) for f in rows], "next_cursor": next_cursor},
    }


@router.get("/data-sets/{name}/versions/{a}/diff/{b}")
def diff_versions(
    name: str,
    a: str,
    b: str,
    principal: Viewer,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 200,
    cursor: str | None = None,
    kind: Literal["added", "removed", "modified"] | None = None,
) -> dict[str, Any]:
    """Changes from version `a` to `b`, ordered by path and paginated: `{added, removed, modified,
    unchanged_count, counts, next_cursor}` (`added`/`removed` items are files; `modified` items
    are `{path, from, to}`). `unchanged_count` and `counts` cover the whole diff, not the page."""
    ds = _load(db, principal, name)
    va, vb = _resolve(db, ds, a), _resolve(db, ds, b)
    changes = service.diff_query(va.id, vb.id, kind)
    stmt = select(changes)
    if cursor:
        stmt = stmt.where(changes.c.path > _decode(cursor))
    rows = db.execute(stmt.order_by(changes.c.path).limit(limit + 1)).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode(rows[-1].path)
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    for r in rows:
        if r.kind == "added":
            added.append({"path": r.path, "sha256": r.to_sha, "size": r.to_size})
        elif r.kind == "removed":
            removed.append({"path": r.path, "sha256": r.from_sha, "size": r.from_size})
        else:
            modified.append({
                "path": r.path,
                "from": {"sha256": r.from_sha, "size": r.from_size},
                "to": {"sha256": r.to_sha, "size": r.to_size},
            })
    counts = service.diff_counts(db, va.id, vb.id)
    return {
        "from_version": va.version,
        "to_version": vb.version,
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": counts.pop("unchanged"),
        "counts": counts,
        "next_cursor": next_cursor,
    }


@router.get("/data-sets/{name}/versions/{version}/files/{file_path:path}")
def download_file(
    name: str,
    version: str,
    file_path: str,
    request: Request,
    principal: Viewer,
    db: DbSession,
    inline: bool = False,
):
    """Content of one file of a version, with the protections of the artifact download: nosniff,
    `sandbox` CSP, no caching, `attachment` unless an inline-safe image type was asked inline, and a
    redirect to a presigned URL on object storage."""
    ds = _load(db, principal, name)
    v = _resolve(db, ds, version)
    f = db.get(DataSetFile, (v.id, file_path))
    if f is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    filename = f.path.rsplit("/", 1)[-1]
    media_type = f.media_type or "application/octet-stream"
    store = get_store(request.app)
    serve_inline = inline and is_inline_type(media_type)
    security = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "private, no-store",
        "X-Content-Sha256": f.sha256,  # lets a client verify the bytes it gets, redirect or not
    }
    if not serve_inline:
        ttl = request.app.state.settings.artifact_url_ttl_seconds
        presigned = store.presign_get(
            principal.project_id, f.sha256, ttl, filename=filename, media_type=media_type
        )
        if presigned is not None:
            return Response(status_code=302, headers={"Location": presigned.url, **security})
    try:
        stream = store.open(principal.project_id, f.sha256)
    except BlobNotFound:
        raise HTTPException(status_code=404, detail="file content not found") from None
    headers = {
        **security,
        "Content-Disposition": content_disposition(filename, inline=serve_inline),
        "Content-Length": str(f.size),
    }
    return StreamingResponse(_iter_file(stream), media_type=media_type, headers=headers)


# --- tags ------------------------------------------------------------------------------------------


class TagSet(BaseModel):
    version: int = Field(ge=1)


@router.get("/data-sets/{name}/tags")
def list_tags(name: str, principal: Viewer, db: DbSession) -> dict[str, Any]:
    ds = _load(db, principal, name)
    latest = service.latest_version(db, ds.id)
    items = [
        {"tag": t.tag, "version": t.version, "updated_at": service.iso(t.updated_at)}
        for t in db.scalars(select(DataSetTag).where(DataSetTag.data_set_id == ds.id).order_by(DataSetTag.tag))
    ]
    if latest is not None:
        items.append({"tag": service.LATEST, "version": latest.version, "updated_at": service.iso(latest.created_at)})
    return {"items": items}


@router.put("/data-sets/{name}/tags/{tag}")
def set_tag(name: str, tag: str, body: TagSet, principal: Member, db: DbSession) -> JSONResponse:
    """Point `tag` at a version (moving it if it exists). `latest` is automatic and read-only."""
    try:
        tag = service.normalize_tag(tag)
    except DataSetError as exc:
        raise _http(exc) from None
    ds = _load(db, principal, name, lock=True)
    if db.scalar(
        select(DataSetVersion.id).where(DataSetVersion.data_set_id == ds.id, DataSetVersion.version == body.version)
    ) is None:
        raise HTTPException(status_code=404, detail="version not found")
    row = db.get(DataSetTag, (ds.id, tag))
    previous = None if row is None else row.version
    if row is None:
        row = DataSetTag(data_set_id=ds.id, project_id=ds.project_id, tag=tag, version=body.version)
        db.add(row)
    else:
        row.version = body.version
    row.updated_by = _created_by(principal)
    row.updated_at = utcnow()
    _audit(db, principal, "data_set.tag.set", ds.id, {"data_set": ds.name, "tag": tag, "version": body.version, "previous": previous})
    return JSONResponse({"tag": tag, "version": body.version, "previous_version": previous})


@router.delete("/data-sets/{name}/tags/{tag}", status_code=204)
def delete_tag(name: str, tag: str, principal: Member, db: DbSession) -> Response:
    ds = _load(db, principal, name, lock=True)
    row = db.get(DataSetTag, (ds.id, tag))
    if row is None:
        raise HTTPException(status_code=404, detail="tag not found")
    db.delete(row)
    _audit(db, principal, "data_set.tag.delete", ds.id, {"data_set": ds.name, "tag": tag})
    return Response(status_code=204)
