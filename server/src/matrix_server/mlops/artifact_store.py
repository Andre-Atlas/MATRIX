"""Artifact storage backends (S3/MinIO SigV4 and LocalFS HMAC) with anti-SSRF & anti-traversal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

import httpx

from matrix_server.config import Settings

_ALLOWED_EXTERNAL_SCHEMES = frozenset({"s3", "gs", "hf", "lakefs"})
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CAS_KEY_RE = re.compile(r"^projects/[^/]+/blobs/sha256/([0-9a-fA-F]{64})$")


class ArtifactTooLargeError(ValueError):
    """Raised when an uploaded artifact exceeds max_artifact_bytes."""


class ArtifactSha256MismatchError(ValueError):
    """Raised when an uploaded artifact's SHA-256 does not match the expected digest."""


class ArtifactCorruptionError(RuntimeError):
    """Raised when an existing CAS blob on disk has a different SHA-256 than expected."""


def _has_control_or_backslash(value: str) -> bool:
    return "\\" in value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def validate_safe_segment(value: str, label: str = "segment") -> str:
    """Validate a single path segment (no slashes, backslashes, dots traversal, or control chars)."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if _has_control_or_backslash(value) or "/" in value or value in {".", ".."} or ".." in value:
        raise ValueError(f"Invalid {label}: path traversal or control characters not allowed")
    return value


def validate_relative_path(path: str, label: str = "path") -> str:
    """Validate a relative posix path (no leading/trailing slash, no '..', no backslashes)."""
    if not isinstance(path, str) or not path:
        raise ValueError(f"{label} must be a non-empty string")
    if _has_control_or_backslash(path) or path.startswith("/") or path.endswith("/"):
        raise ValueError(f"Invalid {label}: must be a clean relative path")
    segments = path.split("/")
    for seg in segments:
        if not seg or seg in {".", ".."} or ".." in seg:
            raise ValueError(f"Invalid {label}: path traversal not allowed")
    return path


def validate_sha256(sha256: str) -> str:
    """Validate and normalize a 64-character hex SHA-256 digest."""
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise ValueError("sha256 must be a 64-character hexadecimal string")
    return sha256.lower()


def validate_external_uri(uri: str) -> str:
    """Validate external artifact URIs against an explicit scheme allowlist and anti-SSRF rules."""
    if not isinstance(uri, str) or not uri:
        raise ValueError("External URI must be a non-empty string")
    if uri != uri.strip() or any(ch.isspace() for ch in uri) or _has_control_or_backslash(uri):
        raise ValueError("External URI contains invalid whitespace or control characters")

    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_EXTERNAL_SCHEMES:
        raise ValueError(
            f"Unsupported external URI scheme '{parsed.scheme}'; "
            f"allowed: {', '.join(sorted(_ALLOWED_EXTERNAL_SCHEMES))}"
        )

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("External URI must not embed credentials")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("External URI must include a valid host/bucket")

    host_lower = hostname.lower().rstrip(".")
    if host_lower == "localhost" or host_lower.endswith((".localhost", ".local", ".internal")):
        raise ValueError("External URI host is not allowed (local/internal hostname)")

    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        ip = None

    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("External URI host IP is not allowed (private/loopback/link-local)")

    for seg in parsed.path.split("/"):
        if seg in {".", ".."}:
            raise ValueError("External URI path must not contain traversal segments")

    return uri


class ArtifactStore(Protocol):
    """Port for content-addressed artifact storage."""

    def object_key(self, project_id: str, sha256: str) -> str: ...

    def presign_put(
        self,
        project_id: str,
        sha256: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str]: ...

    def presign_get(self, project_id: str, storage_uri: str) -> str: ...

    def exists_and_size(self, storage_uri: str) -> tuple[bool, int | None]: ...


def _build_object_key(project_id: str, sha256: str) -> str:
    safe_project = validate_safe_segment(project_id, "project_id")
    safe_sha = validate_sha256(sha256)
    return f"projects/{safe_project}/blobs/sha256/{safe_sha}"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


class LocalFSArtifactStore:
    """Local filesystem artifact store using HMAC-SHA256 signed URLs."""

    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.artifact_dir).resolve()
        self._secret = settings.jwt_secret.encode("utf-8")
        self._ttl_seconds = max(1, int(settings.artifact_presign_ttl_seconds))
        self._max_artifact_bytes = int(settings.max_artifact_bytes)

    def object_key(self, project_id: str, sha256: str) -> str:
        return _build_object_key(project_id, sha256)

    def _resolve_key(self, key: str) -> Path:
        safe_key = validate_relative_path(key, "object_key")
        target = (self._root / safe_key).resolve()
        if target == self._root or not target.is_relative_to(self._root):
            raise ValueError("Artifact path escapes configured root directory")
        return target

    def _sign_token(self, op: Literal["put", "get"], key: str, exp: int) -> str:
        payload = json.dumps({"op": op, "key": key, "exp": exp}, separators=(",", ":"), sort_keys=True)
        payload_b64 = _b64url_encode(payload.encode("utf-8"))
        sig = hmac.new(self._secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{payload_b64}.{sig}"

    def verify_local_token(self, token: str, expected_op: Literal["put", "get"]) -> str:
        """Verify HMAC signature, operation, expiration, and path confinement; return object key."""
        if not isinstance(token, str) or "." not in token:
            raise ValueError("Malformed artifact token")
        parts = token.split(".")
        if len(parts) != 2:
            raise ValueError("Malformed artifact token")
        payload_b64, sig = parts
        expected_sig = hmac.new(self._secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("Invalid artifact token signature")

        try:
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid artifact token payload") from exc

        if type(payload) is not dict:
            raise ValueError("Invalid artifact token payload")
        if payload.get("op") != expected_op:
            raise ValueError("Artifact token operation mismatch")

        exp = payload.get("exp")
        if type(exp) is not int:
            raise ValueError("Invalid artifact token expiration")
        if exp < int(time.time()):
            raise ValueError("Artifact token has expired")

        key = payload.get("key")
        if type(key) is not str:
            raise ValueError("Invalid artifact token key")
        self._resolve_key(key)
        return key

    def presign_put(
        self,
        project_id: str,
        sha256: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str]:
        del content_type  # LocalFS endpoint accepts raw bytes
        if size_bytes < 0 or size_bytes > self._max_artifact_bytes:
            raise ValueError(f"size_bytes must be between 0 and {self._max_artifact_bytes}")
        key = self.object_key(project_id, sha256)
        self._resolve_key(key)
        exp = int(time.time()) + self._ttl_seconds
        token = self._sign_token("put", key, exp)
        return f"local://{key}", f"/api/v1/mlops/artifacts/local/{token}"

    def presign_get(self, project_id: str, storage_uri: str) -> str:
        key = self._extract_local_key(storage_uri)
        safe_project = validate_safe_segment(project_id, "project_id")
        if not key.startswith(f"projects/{safe_project}/"):
            raise ValueError("Artifact does not belong to the requested project")
        exp = int(time.time()) + self._ttl_seconds
        token = self._sign_token("get", key, exp)
        return f"/api/v1/mlops/artifacts/local/{token}"

    def _extract_local_key(self, storage_uri: str) -> str:
        if not isinstance(storage_uri, str) or not storage_uri.startswith("local://"):
            raise ValueError("Invalid local storage URI")
        key = storage_uri[len("local://") :]
        self._resolve_key(key)
        return key

    @staticmethod
    def _expected_sha256_from_key(key: str) -> str | None:
        match = _CAS_KEY_RE.fullmatch(key)
        return match.group(1).lower() if match else None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def write_local_blob(
        self,
        key: str,
        data: bytes,
        *,
        expected_sha256: str | None = None,
    ) -> Path:
        if len(data) > self._max_artifact_bytes:
            raise ArtifactTooLargeError(f"Artifact exceeds max_artifact_bytes ({self._max_artifact_bytes})")
        target = self._resolve_key(key)
        expected = (
            validate_sha256(expected_sha256)
            if expected_sha256 is not None
            else self._expected_sha256_from_key(key)
        )
        actual_sha = hashlib.sha256(data).hexdigest()
        if expected is not None and actual_sha != expected:
            raise ArtifactSha256MismatchError("sha256 mismatch")

        if target.is_file():
            if self._file_sha256(target) == actual_sha:
                return target
            raise ArtifactCorruptionError(f"Existing CAS artifact is corrupted: {key}")

        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target

    async def write_local_blob_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        expected_sha256: str | None = None,
    ) -> tuple[Path, int]:
        target = self._resolve_key(key)
        expected = (
            validate_sha256(expected_sha256)
            if expected_sha256 is not None
            else self._expected_sha256_from_key(key)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(target.parent))
        hasher = hashlib.sha256()
        total_bytes = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > self._max_artifact_bytes:
                        raise ArtifactTooLargeError(
                            f"Artifact exceeds max_artifact_bytes ({self._max_artifact_bytes})"
                        )
                    fh.write(chunk)
                    hasher.update(chunk)

            actual_sha = hasher.hexdigest()
            if expected is not None and actual_sha != expected:
                raise ArtifactSha256MismatchError("sha256 mismatch")

            if target.is_file():
                if self._file_sha256(target) == actual_sha:
                    os.unlink(tmp_name)
                    return target, total_bytes
                raise ArtifactCorruptionError(f"Existing CAS artifact is corrupted: {key}")

            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target, total_bytes

    def read_local_blob_path(self, key: str) -> Path:
        target = self._resolve_key(key)
        if not target.is_file():
            raise FileNotFoundError(f"Artifact blob not found: {key}")
        return target

    def exists_and_size(self, storage_uri: str) -> tuple[bool, int | None]:
        key = self._extract_local_key(storage_uri)
        target = self._resolve_key(key)
        if target.is_file():
            return True, target.stat().st_size
        return False, None


class S3ArtifactStore:
    """Pure-Python AWS SigV4 presigned URL artifact store for MinIO / S3."""

    def __init__(self, settings: Settings, *, http_client: httpx.Client | None = None) -> None:
        self._endpoint = settings.s3_endpoint.rstrip("/")
        parsed = urlsplit(self._endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MATRIX_S3_ENDPOINT must be a valid http/https URL")
        self._scheme = parsed.scheme
        self._netloc = parsed.netloc
        self._base_path = parsed.path.rstrip("/")
        self._bucket = validate_safe_segment(settings.s3_bucket, "s3_bucket")
        self._region = settings.s3_region
        self._access_key = settings.s3_access_key
        self._secret_key = settings.s3_secret_key
        self._ttl_seconds = max(1, int(settings.artifact_presign_ttl_seconds))
        self._max_artifact_bytes = int(settings.max_artifact_bytes)
        self._http_client = http_client

    def object_key(self, project_id: str, sha256: str) -> str:
        return _build_object_key(project_id, sha256)

    def _require_credentials(self) -> tuple[str, str]:
        if not self._access_key or not self._secret_key:
            raise ValueError("S3 credentials (MATRIX_S3_ACCESS_KEY / MATRIX_S3_SECRET_KEY) must be configured")
        return self._access_key, self._secret_key

    def _presign_url(
        self,
        method: Literal["PUT", "GET", "HEAD"],
        key: str,
        *,
        now: datetime | None = None,
    ) -> str:
        access_key, secret_key = self._require_credentials()
        safe_key = validate_relative_path(key, "object_key")

        ts = (now or datetime.now(UTC)).astimezone(UTC)
        amz_date = ts.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = ts.strftime("%Y%m%d")
        credential_scope = f"{date_stamp}/{self._region}/s3/aws4_request"

        canonical_uri = f"{self._base_path}/{quote(self._bucket, safe='')}/{quote(safe_key, safe='/')}"
        query_params = [
            ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
            ("X-Amz-Credential", f"{access_key}/{credential_scope}"),
            ("X-Amz-Date", amz_date),
            ("X-Amz-Expires", str(self._ttl_seconds)),
            ("X-Amz-SignedHeaders", "host"),
        ]
        canonical_query = "&".join(
            f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(query_params, key=lambda x: x[0])
        )
        canonical_headers = f"host:{self._netloc}\n"
        signed_headers = "host"
        canonical_request = (
            f"{method}\n"
            f"{canonical_uri}\n"
            f"{canonical_query}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            "UNSIGNED-PAYLOAD"
        )
        canonical_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{canonical_hash}"

        k_date = hmac.new(f"AWS4{secret_key}".encode(), date_stamp.encode("utf-8"), hashlib.sha256).digest()
        k_region = hmac.new(k_date, self._region.encode("utf-8"), hashlib.sha256).digest()
        k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        return f"{self._scheme}://{self._netloc}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"

    def _extract_s3_key(self, storage_uri: str) -> str:
        if not isinstance(storage_uri, str) or not storage_uri.startswith("s3://"):
            raise ValueError("Invalid S3 storage URI")
        parsed = urlsplit(storage_uri)
        if parsed.netloc != self._bucket:
            raise ValueError("S3 storage URI bucket does not match configured bucket")
        key = parsed.path.lstrip("/")
        return validate_relative_path(key, "object_key")

    def presign_put(
        self,
        project_id: str,
        sha256: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str]:
        del content_type  # host-only SignedHeaders keeps presigned PUT compatible with any client Content-Type
        if size_bytes < 0 or size_bytes > self._max_artifact_bytes:
            raise ValueError(f"size_bytes must be between 0 and {self._max_artifact_bytes}")
        key = self.object_key(project_id, sha256)
        url = self._presign_url("PUT", key)
        return f"s3://{self._bucket}/{key}", url

    def presign_get(self, project_id: str, storage_uri: str) -> str:
        key = self._extract_s3_key(storage_uri)
        safe_project = validate_safe_segment(project_id, "project_id")
        if not key.startswith(f"projects/{safe_project}/"):
            raise ValueError("Artifact does not belong to the requested project")
        return self._presign_url("GET", key)

    def exists_and_size(self, storage_uri: str) -> tuple[bool, int | None]:
        key = self._extract_s3_key(storage_uri)
        head_url = self._presign_url("HEAD", key)
        try:
            if self._http_client is not None:
                resp = self._http_client.head(head_url)
            else:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.head(head_url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Artifact store HEAD request failed: {type(exc).__name__}") from None

        if resp.status_code == 404:
            return False, None
        if resp.status_code == 200:
            raw_len = resp.headers.get("content-length")
            return True, int(raw_len) if raw_len is not None and raw_len.isdigit() else None
        raise RuntimeError(f"Artifact store HEAD returned HTTP {resp.status_code}")


def build_artifact_store(settings: Settings) -> ArtifactStore:
    """Instantiate the configured ArtifactStore backend."""
    if settings.artifact_store == "s3":
        return S3ArtifactStore(settings)
    return LocalFSArtifactStore(settings)
