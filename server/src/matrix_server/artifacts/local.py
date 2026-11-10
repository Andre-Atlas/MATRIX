"""Local filesystem artifact store (dev and tests).

Layout under `root`: `{project_id}/{sha256[0:2]}/{sha256}` for blobs and `{project_id}/.tmp/` for
writes in progress. Directories are 0700, files 0600. A write streams into a temp file in the
project's `.tmp` (same filesystem), hashing as it goes; once complete it is fsynced and renamed into
place atomically (or discarded when the blob already exists, whose mtime is then refreshed so the GC
grace period covers the new reference). Every path is built from a validated project id and hex
digest only, and checked to resolve under `root`.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from matrix_server.artifacts import (
    CHUNK_SIZE,
    BlobInfo,
    BlobNotFound,
    BlobRef,
    BlobTooLarge,
    InvalidKey,
    PresignedRequest,
    check_project_id,
    check_sha256,
    is_sha256,
)

TMP_DIR = ".tmp"


class LocalArtifactStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # --- paths -------------------------------------------------------------------------------

    def _mkdir(self, path: Path) -> None:
        """Create `path` and its missing parents below the root, each with mode 0700."""
        missing = []
        while path != self.root and not path.exists():
            missing.append(path)
            path = path.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise InvalidKey("path escapes the artifact root")
        return resolved

    def _project_dir(self, project_id: str) -> Path:
        return self._inside(self.root / check_project_id(project_id))

    def _blob_path(self, project_id: str, sha256: str) -> Path:
        check_sha256(sha256)
        return self._inside(self._project_dir(project_id) / sha256[:2] / sha256)

    def _tmp_dir(self, project_id: str) -> Path:
        path = self._inside(self._project_dir(project_id) / TMP_DIR)
        self._mkdir(path)
        return path

    # --- contract ----------------------------------------------------------------------------

    def put(self, project_id: str, stream: BinaryIO, *, max_bytes: int) -> BlobInfo:
        tmp_dir = self._tmp_dir(project_id)
        fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, prefix="up-")  # created 0600
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise BlobTooLarge("artifact exceeds the size limit")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            sha = digest.hexdigest()
            final = self._blob_path(project_id, sha)
            if final.exists():
                tmp.unlink(missing_ok=True)
                os.utime(final)
                return BlobInfo(sha, size, created=False)
            self._mkdir(final.parent)
            os.replace(tmp, final)
            return BlobInfo(sha, size, created=True)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def open(self, project_id: str, sha256: str) -> BinaryIO:
        try:
            return open(self._blob_path(project_id, sha256), "rb")
        except FileNotFoundError as exc:
            raise BlobNotFound("blob not found") from exc

    def exists(self, project_id: str, sha256: str) -> bool:
        return self._blob_path(project_id, sha256).is_file()

    def size(self, project_id: str, sha256: str) -> int | None:
        try:
            return self._blob_path(project_id, sha256).stat().st_size
        except FileNotFoundError:
            return None

    def delete(self, project_id: str, sha256: str) -> None:
        self._blob_path(project_id, sha256).unlink(missing_ok=True)

    def presign_put(self, project_id: str, sha256: str, size: int, ttl: int, *, upload_id: str) -> None:
        return None

    def presign_get(
        self, project_id: str, sha256: str, ttl: int, *, filename: str, media_type: str
    ) -> PresignedRequest | None:
        return None

    # --- staging (none locally: the content route writes the final blob) ---------------------

    def stat_upload(self, project_id: str, upload_id: str) -> int | None:
        return None

    def open_upload(self, project_id: str, upload_id: str) -> BinaryIO:
        raise BlobNotFound("no staged upload")

    def promote_upload(self, project_id: str, upload_id: str, sha256: str) -> None:
        raise BlobNotFound("no staged upload")

    def delete_upload(self, project_id: str, upload_id: str) -> None:
        return None

    # --- GC ----------------------------------------------------------------------------------

    def iter_blobs(self) -> Iterator[BlobRef]:
        for project_dir in sorted(self.root.iterdir()):
            if not project_dir.is_dir() or project_dir.is_symlink():
                continue
            try:
                check_project_id(project_dir.name)
            except InvalidKey:
                continue
            for prefix_dir in sorted(project_dir.iterdir()):
                if prefix_dir.name == TMP_DIR or not prefix_dir.is_dir() or prefix_dir.is_symlink():
                    continue
                for blob in sorted(prefix_dir.iterdir()):
                    if is_sha256(blob.name) and blob.name[:2] == prefix_dir.name and blob.is_file():
                        mtime = datetime.fromtimestamp(blob.stat().st_mtime, UTC)
                        yield BlobRef(project_dir.name, blob.name, mtime)

    def purge_stale(self, older_than: datetime) -> int:
        """Remove abandoned temp files (crashed writes) older than `older_than`."""
        cutoff = older_than.timestamp()
        removed = 0
        for project_dir in self.root.iterdir():
            tmp_dir = project_dir / TMP_DIR
            if not tmp_dir.is_dir() or tmp_dir.is_symlink():
                continue
            for tmp in tmp_dir.iterdir():
                try:
                    if tmp.is_file() and tmp.stat().st_mtime < cutoff:
                        tmp.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
        return removed

    def _touch(self, project_id: str, sha256: str, when: float | None = None) -> None:
        """Test helper: set a blob's mtime."""
        when = time.time() if when is None else when
        os.utime(self._blob_path(project_id, sha256), (when, when))
