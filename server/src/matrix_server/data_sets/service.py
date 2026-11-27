"""Rules of the file data-sets (contract G3 section 2): path and manifest validation, canonical
manifest hash, immutable versions (idempotent on the latest manifest), tags, diff and lookups.

Consistency with the artifact store: a version only references artifacts of its own project that
are ready. Creating a version holds the project's quota lock (`lock_quota`) from the artifact
lookup until the commit, and the artifact DELETE route takes the same lock before asking
`blob_in_use`, so an artifact cannot disappear between the check and the commit of a version.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import BigInteger, String, and_, cast, func, literal_column, not_, null, select, union_all
from sqlalchemy.orm import Session, aliased

from matrix_server.artifacts.models import Artifact
from matrix_server.artifacts.service import lock_quota, normalize_media_type
from matrix_server.data_sets.models import DataSet, DataSetFile, DataSetTag, DataSetVersion
from matrix_server.storage.db import as_utc

DEFAULT_MAX_FILES_PER_VERSION = 100_000  # contract G3 section 2; `data_set_max_files` setting overrides
MAX_PATH_CHARS = 1024
MAX_SEGMENT_CHARS = 255
LATEST = "latest"
DATA_SET_KINDS = ("files",)
SOURCE_TYPES = ("upload",)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_IN_CHUNK = 500


class DataSetError(Exception):
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidInput(DataSetError):
    status_code = 422


class NotFound(DataSetError):
    status_code = 404


class Conflict(DataSetError):
    status_code = 409


def max_files(settings: Any) -> int:
    # TODO(coordinator): add `data_set_max_files: int = 100_000` (MATRIX_DATA_SET_MAX_FILES) to Settings.
    value = getattr(settings, "data_set_max_files", None)
    return int(value) if value else DEFAULT_MAX_FILES_PER_VERSION


# --- validation -----------------------------------------------------------------------------------


def normalize_name(raw: str) -> str:
    if not isinstance(raw, str) or not _NAME_RE.fullmatch(raw):
        raise InvalidInput("invalid data-set name (letters, digits, '.', '_' and '-', up to 128)")
    return raw


def normalize_tag(raw: str) -> str:
    if not isinstance(raw, str) or not _TAG_RE.fullmatch(raw):
        raise InvalidInput("invalid tag (starts with a letter; letters, digits, '.', '_' and '-')")
    if raw == LATEST:
        raise InvalidInput("'latest' is automatic and cannot be set")
    return raw


def normalize_path(raw: str) -> str:
    """A relative POSIX path in NFC: no empty, `.` or `..` segments, no leading or trailing `/`, no
    backslashes, drive letters or control characters, at most 1024 characters. Rejected, never
    silently rewritten, so the manifest hash means what the client sent."""
    if not isinstance(raw, str) or not raw:
        raise InvalidInput("file path is required")
    path = unicodedata.normalize("NFC", raw)
    if len(path) > MAX_PATH_CHARS:
        raise InvalidInput(f"file path exceeds {MAX_PATH_CHARS} characters")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in path):
        raise InvalidInput("file path contains control characters")
    if "\\" in path:
        raise InvalidInput("file path must use '/' separators")
    if path.startswith("/") or _DRIVE_RE.match(path):
        raise InvalidInput("file path must be relative")
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            raise InvalidInput("file path must be normalized (no empty, '.' or '..' segments)")
        if len(segment) > MAX_SEGMENT_CHARS:
            raise InvalidInput(f"file path segment exceeds {MAX_SEGMENT_CHARS} characters")
    return path


@dataclass(frozen=True)
class ResolvedFile:
    path: str
    sha256: str
    size: int
    artifact_id: str | None
    media_type: str | None


def manifest_hash(files: Iterable[ResolvedFile]) -> str:
    """sha256 of the canonical manifest: JSON array of `{path, sha256, size}` sorted by path (UTF-8
    code points), compact separators. Media types and artifact ids are not part of the identity."""
    ordered = sorted(files, key=lambda f: f.path)
    payload = [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in ordered]
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def _chunks(items: Sequence[str]) -> Iterable[list[str]]:
    for i in range(0, len(items), _IN_CHUNK):
        yield list(items[i : i + _IN_CHUNK])


def resolve_manifest(
    db: Session, project_id: str, entries: Sequence[Any], *, limit: int
) -> list[ResolvedFile]:
    """Validate manifest entries (objects with `path`, `artifact_id`, `sha256`, `size`,
    `media_type`) and bind each to a ready artifact of the project. Call it holding the quota lock."""
    if not entries:
        raise InvalidInput("a version needs at least one file")
    if len(entries) > limit:
        raise InvalidInput(f"a version holds at most {limit} files")
    paths: dict[str, Any] = {}
    for entry in entries:
        path = normalize_path(entry.path)
        if path in paths:
            raise InvalidInput(f"duplicate file path {path!r}")
        paths[path] = entry
    for path in paths:  # a file cannot also be a directory
        parts = path.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in paths:
                raise InvalidInput(f"path {path!r} conflicts with file {'/'.join(parts[:i])!r}")

    ids = sorted({e.artifact_id for e in entries if e.artifact_id})
    shas = sorted({e.sha256.lower() for e in entries if not e.artifact_id and e.sha256})
    by_id: dict[str, Artifact] = {}
    by_sha: dict[str, Artifact] = {}
    for batch in _chunks(ids):
        for a in db.scalars(
            select(Artifact).where(
                Artifact.project_id == project_id, Artifact.id.in_(batch), Artifact.status == "ready"
            )
        ):
            by_id[a.id] = a
    for batch in _chunks(shas):
        for a in db.scalars(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.sha256.in_(batch),
                Artifact.status == "ready",
                Artifact.verified.is_(True),
            )
            .order_by(Artifact.created_at, Artifact.id)
        ):
            by_sha.setdefault(a.sha256, a)

    out: list[ResolvedFile] = []
    for path in sorted(paths):
        entry = paths[path]
        if entry.artifact_id:
            art = by_id.get(entry.artifact_id)
            if art is None:
                raise InvalidInput(f"{path}: artifact not found in this project")
            if entry.sha256 and entry.sha256.lower() != art.sha256:
                raise InvalidInput(f"{path}: sha256 does not match the artifact")
        else:
            art = by_sha.get(entry.sha256.lower()) if entry.sha256 else None
            if art is None:
                raise InvalidInput(f"{path}: no artifact with this sha256 in this project")
        if entry.size is not None and entry.size != art.size:
            raise InvalidInput(f"{path}: size does not match the artifact")
        try:
            media_type = normalize_media_type(entry.media_type) if entry.media_type else art.media_type
        except Exception:  # noqa: BLE001 - ArtifactError
            raise InvalidInput(f"{path}: invalid media type") from None
        out.append(ResolvedFile(path, art.sha256, art.size, art.id, media_type))
    return out


# --- lookups --------------------------------------------------------------------------------------


def get_data_set(db: Session, project_id: str, name: str, *, lock: bool = False) -> DataSet:
    stmt = select(DataSet).where(DataSet.project_id == project_id, DataSet.name == name)
    if lock:
        stmt = stmt.with_for_update()
    ds = db.scalar(stmt)
    if ds is None:
        raise NotFound("data-set not found")
    return ds


def latest_version(db: Session, data_set_id: str) -> DataSetVersion | None:
    return db.scalar(
        select(DataSetVersion)
        .where(DataSetVersion.data_set_id == data_set_id)
        .order_by(DataSetVersion.version.desc())
        .limit(1)
    )


def resolve_version(db: Session, ds: DataSet, ref: str) -> DataSetVersion:
    """`ref` is a version number (`3` or `v3`), `latest` or a tag."""
    ref = ref.strip()
    number: int | None = None
    if ref == LATEST:
        v = latest_version(db, ds.id)
        if v is None:
            raise NotFound("data-set has no versions")
        return v
    if ref.isdecimal() and len(ref) <= 9:
        number = int(ref)
    elif re.fullmatch(r"[vV]\d{1,9}", ref):
        number = int(ref[1:])
    else:
        tag = db.get(DataSetTag, (ds.id, ref))
        if tag is None:
            raise NotFound("version or tag not found")
        number = tag.version
    v = db.scalar(
        select(DataSetVersion).where(DataSetVersion.data_set_id == ds.id, DataSetVersion.version == number)
    )
    if v is None:
        raise NotFound("version not found")
    return v


def version_by_id(db: Session, project_id: str, version_id: str) -> DataSetVersion | None:
    return db.scalar(
        select(DataSetVersion).where(DataSetVersion.id == version_id, DataSetVersion.project_id == project_id)
    )


def classify_version_ref(db: Session, project_id: str, ref: str) -> str | None:
    """`own` when `ref` is the id of a data-set version of the project, `foreign` when it belongs to
    another project, else None (not a data-set version)."""
    row = db.scalar(select(DataSetVersion.project_id).where(DataSetVersion.id == ref))
    if row is None:
        return None
    return "own" if row == project_id else "foreign"


def tags_by_version(db: Session, data_set_id: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for t in db.scalars(select(DataSetTag).where(DataSetTag.data_set_id == data_set_id).order_by(DataSetTag.tag)):
        out.setdefault(t.version, []).append(t.tag)
    return out


# --- versions -------------------------------------------------------------------------------------


def create_version(
    db: Session,
    ds: DataSet,
    files: Sequence[ResolvedFile],
    *,
    message: str | None,
    parent_version: int | None,
    created_by: str | None,
) -> tuple[DataSetVersion, bool]:
    """Add the next version of `ds` (a row locked by the caller) or, when `files` hash to the
    latest version's manifest, return that one. `(version, created)`; the caller commits."""
    digest = manifest_hash(files)
    latest = latest_version(db, ds.id)
    if latest is not None and latest.manifest_sha256 == digest:
        return latest, False
    if parent_version is None:
        parent_version = latest.version if latest is not None else None
    elif db.scalar(
        select(DataSetVersion.id).where(
            DataSetVersion.data_set_id == ds.id, DataSetVersion.version == parent_version
        )
    ) is None:
        raise InvalidInput("parent_version not found")
    version = DataSetVersion(
        project_id=ds.project_id,
        data_set_id=ds.id,
        version=(latest.version + 1) if latest is not None else 1,
        parent_version=parent_version,
        manifest_sha256=digest,
        file_count=len(files),
        total_bytes=sum(f.size for f in files),
        message=message,
        source_type="upload",
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    db.execute(
        DataSetFile.__table__.insert(),
        [
            {
                "version_id": version.id,
                "path": f.path,
                "project_id": ds.project_id,
                "sha256": f.sha256,
                "size": f.size,
                "artifact_id": f.artifact_id,
                "media_type": f.media_type,
            }
            for f in files
        ],
    )
    return version, True


def lock_for_new_version(db: Session, project_id: str) -> None:
    lock_quota(db, project_id)


def blob_in_use(db: Session, project_id: str, sha256: str) -> bool:
    """Some data-set version of the project lists a file with this content."""
    return (
        db.scalar(
            select(DataSetFile.version_id)
            .where(DataSetFile.project_id == project_id, DataSetFile.sha256 == sha256)
            .limit(1)
        )
        is not None
    )


def referenced_by_runs(db: Session, project_id: str, data_set_id: str) -> int:
    from matrix_server.runs.models import Run

    ids = select(DataSetVersion.id).where(DataSetVersion.data_set_id == data_set_id)
    return int(
        db.scalar(
            select(func.count()).select_from(Run).where(Run.project_id == project_id, Run.dataset_version_id.in_(ids))
        )
        or 0
    )


# --- diff -----------------------------------------------------------------------------------------


def diff_query(a_id: str, b_id: str, kind: str | None = None):
    """UNION ALL of added / removed / modified rows between versions `a` and `b`, ordered by the
    caller on `path`: `(kind, path, from_sha, from_size, to_sha, to_size)`. Pure SQL, so the
    ordering used for keyset pagination is the database's own."""
    fa, fb = aliased(DataSetFile), aliased(DataSetFile)
    null_s, null_i = cast(null(), String(64)), cast(null(), BigInteger)
    parts = []
    if kind in (None, "added"):
        parts.append(
            select(literal_column("'added'").label("kind"), fb.path, null_s.label("from_sha"), null_i.label("from_size"),
                   fb.sha256.label("to_sha"), fb.size.label("to_size"))
            .where(fb.version_id == b_id, not_(select(fa.path).where(fa.version_id == a_id, fa.path == fb.path).exists()))
        )
    if kind in (None, "removed"):
        parts.append(
            select(literal_column("'removed'").label("kind"), fa.path, fa.sha256.label("from_sha"), fa.size.label("from_size"),
                   null_s.label("to_sha"), null_i.label("to_size"))
            .where(fa.version_id == a_id, not_(select(fb.path).where(fb.version_id == b_id, fb.path == fa.path).exists()))
        )
    if kind in (None, "modified"):
        parts.append(
            select(literal_column("'modified'").label("kind"), fa.path, fa.sha256.label("from_sha"), fa.size.label("from_size"),
                   fb.sha256.label("to_sha"), fb.size.label("to_size"))
            .join(fb, and_(fb.version_id == b_id, fb.path == fa.path))
            .where(fa.version_id == a_id, fa.sha256 != fb.sha256)
        )
    return union_all(*parts).subquery("changes")


def diff_counts(db: Session, a_id: str, b_id: str) -> dict[str, int]:
    changes = diff_query(a_id, b_id)
    counts = {"added": 0, "removed": 0, "modified": 0}
    for kind, n in db.execute(select(changes.c.kind, func.count()).group_by(changes.c.kind)):
        counts[kind] = int(n)
    fa, fb = aliased(DataSetFile), aliased(DataSetFile)
    counts["unchanged"] = int(
        db.scalar(
            select(func.count())
            .select_from(fa)
            .join(fb, and_(fb.version_id == b_id, fb.path == fa.path))
            .where(fa.version_id == a_id, fa.sha256 == fb.sha256)
        )
        or 0
    )
    return counts


def iso(ts) -> str | None:
    ts = as_utc(ts)
    return None if ts is None else ts.isoformat().replace("+00:00", "Z")
