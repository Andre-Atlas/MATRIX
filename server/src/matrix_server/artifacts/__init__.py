"""Content-addressed artifact store, scoped by project (ADR-007, contract G0 section 2).

Object key = `{project_id}/{sha256[0:2]}/{sha256}`. Nothing supplied by the user (file names, owner
ids) ever reaches a disk path or an S3 key: only a validated project id, a validated hex digest and,
for presigned uploads, a validated upload id (the artifact id).

Backends: `local.LocalArtifactStore` (dev/tests) and `s3.S3ArtifactStore` (boto3, S3-compatible).
Metadata lives in the `artifacts` table (`artifacts.models`); quota, completion and GC logic in
`artifacts.service`.

Besides the contract methods, both backends implement the staging operations used by the presigned
flow (`stat_upload`, `open_upload`, `promote_upload`, `delete_upload`) and the GC enumeration
(`iter_blobs`, `purge_stale`). The local backend has no staging area: its presigned "URL" is the API
route `PUT /api/v1/artifacts/{id}/content`, which writes the final blob directly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO, Protocol
from urllib.parse import quote

CHUNK_SIZE = 1024 * 1024

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(Exception):
    """Base error of the store; messages never contain user-supplied names."""


class BlobTooLarge(ArtifactStoreError):
    """The stream went over `max_bytes`; nothing was persisted."""


class BlobNotFound(ArtifactStoreError):
    pass


class InvalidKey(ArtifactStoreError, ValueError):
    pass


@dataclass(frozen=True)
class BlobInfo:
    sha256: str
    size: int
    created: bool = True  # False when the blob already existed in the project (dedup)


@dataclass(frozen=True)
class PresignedRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: datetime | None = None


@dataclass(frozen=True)
class BlobRef:
    project_id: str
    sha256: str
    modified: datetime


def check_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
        raise InvalidKey("invalid project id")
    return project_id


def check_sha256(sha256: str) -> str:
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise InvalidKey("invalid sha256")
    return sha256


def is_sha256(value: str | None) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _ascii_filename(filename: str) -> str:
    return "".join(c if 32 <= ord(c) < 127 and c not in '"\\;' else "_" for c in filename) or "download"


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """RFC 6266 header with an ASCII fallback and the UTF-8 name (RFC 8187)."""
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{_ascii_filename(filename)}\"; filename*=UTF-8''{quote(filename, safe='')}"


def object_key(project_id: str, sha256: str) -> str:
    check_project_id(project_id)
    check_sha256(sha256)
    return f"{project_id}/{sha256[:2]}/{sha256}"


class ArtifactStore(Protocol):
    def put(self, project_id: str, stream: BinaryIO, *, max_bytes: int) -> BlobInfo: ...
    def open(self, project_id: str, sha256: str) -> BinaryIO: ...
    def exists(self, project_id: str, sha256: str) -> bool: ...
    def delete(self, project_id: str, sha256: str) -> None: ...
    def presign_put(
        self, project_id: str, sha256: str, size: int, ttl: int, *, upload_id: str
    ) -> PresignedRequest | None: ...
    def presign_get(
        self, project_id: str, sha256: str, ttl: int, *, filename: str, media_type: str
    ) -> PresignedRequest | None: ...

    # --- extensions (presigned staging and GC) ---
    def size(self, project_id: str, sha256: str) -> int | None: ...
    def stat_upload(self, project_id: str, upload_id: str) -> int | None: ...
    def open_upload(self, project_id: str, upload_id: str) -> BinaryIO: ...
    def promote_upload(self, project_id: str, upload_id: str, sha256: str) -> None: ...
    def delete_upload(self, project_id: str, upload_id: str) -> None: ...
    def iter_blobs(self) -> Iterator[BlobRef]: ...
    def purge_stale(self, older_than: datetime) -> int: ...


def build_artifact_store(settings: Any) -> ArtifactStore:
    """Build the backend named by `settings.artifact_backend` (`local` by default)."""
    backend = getattr(settings, "artifact_backend", "local")
    if backend == "s3":
        from matrix_server.artifacts.s3 import S3ArtifactStore

        return S3ArtifactStore.from_settings(settings)
    if backend == "local":
        from matrix_server.artifacts.local import LocalArtifactStore

        return LocalArtifactStore(getattr(settings, "artifact_local_dir", "./artifacts"))
    raise ValueError(f"unknown artifact backend {backend!r}")


__all__ = [
    "CHUNK_SIZE",
    "ArtifactStore",
    "ArtifactStoreError",
    "BlobInfo",
    "BlobNotFound",
    "BlobRef",
    "BlobTooLarge",
    "InvalidKey",
    "PresignedRequest",
    "build_artifact_store",
    "check_project_id",
    "check_sha256",
    "content_disposition",
    "is_sha256",
    "object_key",
]
