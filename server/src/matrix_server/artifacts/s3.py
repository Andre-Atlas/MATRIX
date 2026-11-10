"""S3-compatible artifact store (boto3; tested against RustFS). ADR-007.

Keys in the bucket:
- `{project_id}/{sha256[0:2]}/{sha256}` — blobs (content-addressed, scoped by project);
- `{project_id}/tmp/{random}` — multipart writes in progress (promoted by copy once hashed);
- `{project_id}/uploads/{upload_id}` — presigned PUT targets, promoted by `promote_upload` after
  the API has checked size and hash. A presigned PUT never targets a blob key, so a client cannot
  overwrite content other artifacts already reference.

Path-style addressing and SigV4. Streams up to `part_size` (8 MiB) go in one `put_object` straight
to the blob key (the hash is known before writing); larger streams use a multipart upload to a temp
key, aborted on any error or when `max_bytes` is crossed, so nothing partial is left behind.
Presigned URLs are signed against `public_endpoint` when set (the address browsers/SDKs reach).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from matrix_server.artifacts import (
    CHUNK_SIZE,
    BlobInfo,
    BlobNotFound,
    BlobRef,
    BlobTooLarge,
    InvalidKey,
    PresignedRequest,
    check_project_id,
    content_disposition,
    is_sha256,
    object_key,
)

PART_SIZE = 8 * 1024 * 1024
_NOT_FOUND = {"404", "NoSuchKey", "NotFound", "NoSuchBucket", "NoSuchUpload"}


def _is_not_found(exc: ClientError) -> bool:
    err = exc.response.get("Error", {})
    status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return str(err.get("Code", "")) in _NOT_FOUND or status == "404"


def _read_up_to(stream: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(min(CHUNK_SIZE, n - len(buf)))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


class S3ArtifactStore:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        public_endpoint: str | None = None,
        auto_create_bucket: bool = False,
        part_size: int = PART_SIZE,
    ) -> None:
        if part_size < 5 * 1024 * 1024:
            raise ValueError("S3 multipart parts must be at least 5 MiB")
        self.bucket = bucket
        self.region = region
        self.part_size = part_size
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        )
        common: dict[str, Any] = {
            "region_name": region,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "config": config,
        }
        self.client = boto3.client("s3", endpoint_url=endpoint, **common)
        self.signer = (
            boto3.client("s3", endpoint_url=public_endpoint, **common)
            if public_endpoint and public_endpoint != endpoint
            else self.client
        )
        if auto_create_bucket:
            self.ensure_bucket()

    @classmethod
    def from_settings(cls, settings: Any) -> S3ArtifactStore:
        return cls(
            bucket=settings.s3_bucket,
            endpoint=settings.s3_endpoint,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            public_endpoint=settings.s3_public_endpoint,
            auto_create_bucket=settings.s3_auto_create_bucket,
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            if not _is_not_found(exc):
                raise
        kwargs: dict[str, Any] = {"Bucket": self.bucket}
        if self.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        try:
            self.client.create_bucket(**kwargs)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("BucketAlreadyOwnedByYou",):
                raise

    # --- keys --------------------------------------------------------------------------------

    @staticmethod
    def _upload_key(project_id: str, upload_id: str) -> str:
        check_project_id(project_id)
        try:
            check_project_id(upload_id)
        except InvalidKey as exc:
            raise InvalidKey("invalid upload id") from exc
        return f"{project_id}/uploads/{upload_id}"

    def _head(self, key: str) -> dict[str, Any] | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise

    def _touch(self, key: str) -> None:
        """Refresh LastModified so the GC grace period covers a new reference (best effort)."""
        try:
            self.client.copy_object(
                Bucket=self.bucket,
                Key=key,
                CopySource={"Bucket": self.bucket, "Key": key},
                MetadataDirective="REPLACE",
                Metadata={"touched": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")},
            )
        except ClientError:
            pass

    def _promote(self, src_key: str, project_id: str, sha256: str) -> bool:
        """Copy `src_key` to the blob key unless the blob exists; always delete `src_key`."""
        final = object_key(project_id, sha256)
        try:
            if self._head(final) is not None:
                self._touch(final)
                return False
            self.client.copy({"Bucket": self.bucket, "Key": src_key}, self.bucket, final)
            return True
        finally:
            self.client.delete_object(Bucket=self.bucket, Key=src_key)

    # --- contract ----------------------------------------------------------------------------

    def put(self, project_id: str, stream: BinaryIO, *, max_bytes: int) -> BlobInfo:
        check_project_id(project_id)
        digest = hashlib.sha256()
        first = _read_up_to(stream, self.part_size)  # shorter than part_size only at EOF
        if len(first) > max_bytes:
            raise BlobTooLarge("artifact exceeds the size limit")
        digest.update(first)
        if len(first) < self.part_size:
            sha = digest.hexdigest()
            final = object_key(project_id, sha)
            if self._head(final) is not None:
                self._touch(final)
                return BlobInfo(sha, len(first), created=False)
            self.client.put_object(Bucket=self.bucket, Key=final, Body=first)
            return BlobInfo(sha, len(first), created=True)
        return self._put_multipart(project_id, stream, first, digest, max_bytes)

    def _put_multipart(
        self, project_id: str, stream: BinaryIO, pending: bytes, digest: Any, max_bytes: int
    ) -> BlobInfo:
        tmp_key = f"{project_id}/tmp/{uuid.uuid4().hex}"
        upload_id = self.client.create_multipart_upload(Bucket=self.bucket, Key=tmp_key)["UploadId"]
        parts: list[dict[str, Any]] = []
        size = len(pending)
        buf = bytearray(pending)
        try:
            while True:
                # Fill the buffer to a full part (or EOF), enforcing max_bytes as data arrives.
                eof = False
                while len(buf) < self.part_size:
                    chunk = stream.read(min(CHUNK_SIZE, self.part_size - len(buf)))
                    if not chunk:
                        eof = True
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise BlobTooLarge("artifact exceeds the size limit")
                    digest.update(chunk)
                    buf += chunk
                if buf or not parts:
                    number = len(parts) + 1
                    resp = self.client.upload_part(
                        Bucket=self.bucket, Key=tmp_key, UploadId=upload_id, PartNumber=number, Body=bytes(buf)
                    )
                    parts.append({"PartNumber": number, "ETag": resp["ETag"]})
                    buf = bytearray()
                if eof:
                    break
            self.client.complete_multipart_upload(
                Bucket=self.bucket, Key=tmp_key, UploadId=upload_id, MultipartUpload={"Parts": parts}
            )
        except BaseException:
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=tmp_key, UploadId=upload_id)
            except ClientError:
                pass
            raise
        sha = digest.hexdigest()
        created = self._promote(tmp_key, project_id, sha)
        return BlobInfo(sha, size, created=created)

    def open(self, project_id: str, sha256: str) -> BinaryIO:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=object_key(project_id, sha256))["Body"]
        except ClientError as exc:
            if _is_not_found(exc):
                raise BlobNotFound("blob not found") from exc
            raise

    def exists(self, project_id: str, sha256: str) -> bool:
        return self._head(object_key(project_id, sha256)) is not None

    def size(self, project_id: str, sha256: str) -> int | None:
        head = self._head(object_key(project_id, sha256))
        return None if head is None else int(head["ContentLength"])

    def delete(self, project_id: str, sha256: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key(project_id, sha256))

    def presign_put(
        self, project_id: str, sha256: str, size: int, ttl: int, *, upload_id: str
    ) -> PresignedRequest:
        key = self._upload_key(project_id, upload_id)
        url = self.signer.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": "application/octet-stream"},
            ExpiresIn=ttl,
            HttpMethod="PUT",
        )
        return PresignedRequest(
            method="PUT",
            url=url,
            headers={"Content-Type": "application/octet-stream"},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )

    def presign_get(
        self, project_id: str, sha256: str, ttl: int, *, filename: str, media_type: str
    ) -> PresignedRequest:
        url = self.signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key(project_id, sha256),
                "ResponseContentDisposition": content_disposition(filename),
                "ResponseContentType": media_type,
            },
            ExpiresIn=ttl,
            HttpMethod="GET",
        )
        return PresignedRequest(method="GET", url=url, expires_at=datetime.now(UTC) + timedelta(seconds=ttl))

    # --- staging -----------------------------------------------------------------------------

    def stat_upload(self, project_id: str, upload_id: str) -> int | None:
        head = self._head(self._upload_key(project_id, upload_id))
        return None if head is None else int(head["ContentLength"])

    def open_upload(self, project_id: str, upload_id: str) -> BinaryIO:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._upload_key(project_id, upload_id))[
                "Body"
            ]
        except ClientError as exc:
            if _is_not_found(exc):
                raise BlobNotFound("no staged upload") from exc
            raise

    def promote_upload(self, project_id: str, upload_id: str, sha256: str) -> None:
        self._promote(self._upload_key(project_id, upload_id), project_id, sha256)

    def delete_upload(self, project_id: str, upload_id: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._upload_key(project_id, upload_id))

    # --- GC ----------------------------------------------------------------------------------

    def _iter_objects(self) -> Iterator[dict[str, Any]]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            yield from page.get("Contents", [])

    def iter_blobs(self) -> Iterator[BlobRef]:
        for obj in self._iter_objects():
            parts = obj["Key"].split("/")
            if len(parts) != 3:
                continue
            project_id, prefix, sha = parts
            try:
                check_project_id(project_id)
            except InvalidKey:
                continue
            if is_sha256(sha) and prefix == sha[:2]:
                yield BlobRef(project_id, sha, obj["LastModified"])

    def purge_stale(self, older_than: datetime) -> int:
        """Delete temp and staged objects older than `older_than`; abort stale multipart uploads."""
        removed = 0
        for obj in list(self._iter_objects()):
            parts = obj["Key"].split("/")
            if len(parts) == 3 and parts[1] in ("tmp", "uploads") and obj["LastModified"] < older_than:
                self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
                removed += 1
        try:
            paginator = self.client.get_paginator("list_multipart_uploads")
            for page in paginator.paginate(Bucket=self.bucket):
                for up in page.get("Uploads", []):
                    if up["Initiated"] < older_than:
                        self.client.abort_multipart_upload(
                            Bucket=self.bucket, Key=up["Key"], UploadId=up["UploadId"]
                        )
                        removed += 1
        except ClientError:
            pass  # not every S3-compatible server lists multipart uploads; set a lifecycle rule there
        return removed


__all__ = ["PART_SIZE", "S3ArtifactStore"]
